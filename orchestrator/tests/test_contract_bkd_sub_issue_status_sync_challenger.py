"""Challenger contract tests for REQ-fix-bkd-sub-issue-status-sync-1777426309.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-bkd-sub-issue-status-sync-1777426309/specs/bkd-status-sync/spec.md

Scenarios:
  BSS-S1  transient BKD error → retry succeeds (exponential backoff 1s, 2s)
  BSS-S2  persistent BKD failure → 3 attempts, warning log, no raise
  BSS-S3  completed analyze issue stuck in review → patched to done
  BSS-S4  verifier issue skipped (preserve escalate resume path)
  BSS-S5  running session skipped
  BSS-S6  non-review status skipped
  BSS-S7  issue without REQ tag skipped
  BSS-S8  individual PATCH failure does not abort batch
  BSS-S9  multiple projects scanned

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from orchestrator.bkd import Issue


# ─── Fake BKD for webhook retry tests ────────────────────────────────────────


class _FakeBKDFlaky:
    """Fails first N calls to update_issue, then succeeds."""

    captured: ClassVar[list[tuple[str, str, str]]] = []
    fail_count: ClassVar[int] = 0

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def update_issue(self, *, project_id, issue_id, status_id):
        fails_so_far = len([c for c in self.captured if c[2].startswith("fail-")])
        if fails_so_far < self.fail_count:
            self.captured.append((project_id, issue_id, f"fail-{status_id}"))
            raise RuntimeError(f"BKD down (attempt {fails_so_far + 1})")
        self.captured.append((project_id, issue_id, status_id))


class _FakeBKDPersistentFail:
    """Always fails update_issue."""

    captured: ClassVar[list[tuple[str, str, str]]] = []

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def update_issue(self, *, project_id, issue_id, status_id):
        self.captured.append((project_id, issue_id, status_id))
        raise RuntimeError("BKD 500")


# ─── Fake pool for watchdog tests ────────────────────────────────────────────


@dataclass
class _FakePool:
    rows: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        return self.rows

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        pass


# ─── BSS-S1: transient BKD error succeeds on retry ───────────────────────────


@pytest.mark.asyncio
async def test_bss_s1_transient_bkd_error_succeeds_on_retry(monkeypatch, caplog):
    """BSS-S1: BKD fails first 2 attempts; _push_upstream_status retries with backoff.

    - exactly 3 update_issue attempts
    - 2nd attempt sleeps 1.0s before retry
    - 3rd attempt sleeps 2.0s before retry
    - function returns without raising
    """
    from orchestrator import webhook

    _FakeBKDFlaky.captured = []
    _FakeBKDFlaky.fail_count = 2
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKDFlaky)

    sleep_calls: list[float] = []
    orig_sleep = asyncio.sleep

    async def _tracked_sleep(delay):
        sleep_calls.append(delay)
        # no actual sleep in test

    monkeypatch.setattr(asyncio, "sleep", _tracked_sleep)

    # Should not raise
    await webhook._push_upstream_status("proj-1", "iss-a", "done")

    # Exactly 3 attempts total
    attempts = [c for c in _FakeBKDFlaky.captured if c[1] == "iss-a"]
    assert len(attempts) == 3, (
        f"BSS-S1: MUST attempt update_issue exactly 3 times; got {len(attempts)}"
    )

    # First 2 are failures (prefixed with fail-)
    assert attempts[0][2] == "fail-done", "BSS-S1: first attempt MUST be a failure"
    assert attempts[1][2] == "fail-done", "BSS-S1: second attempt MUST be a failure"
    # Third succeeds
    assert attempts[2][2] == "done", "BSS-S1: third attempt MUST succeed"

    # Sleep delays: 1.0s before 2nd attempt, 2.0s before 3rd attempt
    assert sleep_calls == [1.0, 2.0], (
        f"BSS-S1: backoff delays MUST be [1.0, 2.0]; got {sleep_calls!r}"
    )


# ─── BSS-S2: persistent BKD failure is swallowed after 3 attempts ────────────


@pytest.mark.asyncio
async def test_bss_s2_persistent_bkd_failure_swallowed_after_3(monkeypatch):
    """BSS-S2: BKD fails all 3 attempts; _push_upstream_status logs warning and returns.

    - exactly 3 update_issue attempts
    - warning logged with 'webhook.upstream_status_failed'
    - returns without raising an exception
    """
    from orchestrator import webhook

    _FakeBKDPersistentFail.captured = []
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKDPersistentFail)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())  # skip real sleeps

    with structlog.testing.capture_logs() as log_records:
        # Must not raise
        await webhook._push_upstream_status("proj-1", "iss-b", "done")

    attempts = [c for c in _FakeBKDPersistentFail.captured if c[1] == "iss-b"]
    assert len(attempts) == 3, (
        f"BSS-S2: MUST attempt update_issue exactly 3 times; got {len(attempts)}"
    )

    # Warning must be logged
    assert any("webhook.upstream_status_failed" in (r.get("event", "") or str(r)) for r in log_records), (
        "BSS-S2: MUST log warning containing 'webhook.upstream_status_failed'"
    )


# ─── Fake BKD for watchdog compensation tests ────────────────────────────────


def _patch_bkd_for_sync(monkeypatch, issues_by_project: dict[str, list[Issue]],
                        patch_raises: set[str] | None = None):
    """Mock BKDClient for watchdog sync: list_issues + update_issue."""
    patch_raises = patch_raises or set()
    captured_updates: list[tuple[str, str, str]] = []

    @asynccontextmanager
    async def _ctx(*a, **kw):
        fake = AsyncMock()

        async def _list_issues(project_id: str, limit: int = 200) -> list[Issue]:
            return issues_by_project.get(project_id, [])

        async def _update_issue(project_id: str, issue_id: str, *, status_id: str | None = None,
                                tags: list[str] | None = None, title: str | None = None,
                                description: str | None = None) -> Issue:
            key = f"{project_id}:{issue_id}"
            if key in patch_raises:
                raise RuntimeError("BKD PATCH 500")
            captured_updates.append((project_id, issue_id, status_id or ""))
            return Issue(
                id=issue_id, project_id=project_id, issue_number=0,
                title="", status_id=status_id or "", tags=tags or [],
                session_status="completed",
            )

        fake.list_issues = _list_issues
        fake.update_issue = _update_issue
        yield fake

    monkeypatch.setattr("orchestrator.watchdog.BKDClient", _ctx)
    return captured_updates


def _make_issue(
    issue_id: str,
    status_id: str,
    session_status: str,
    tags: list[str],
    project_id: str = "proj-1",
) -> Issue:
    return Issue(
        id=issue_id, project_id=project_id, issue_number=0,
        title="", status_id=status_id, tags=tags,
        session_status=session_status,
    )


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


# ─── BSS-S3: completed analyze issue stuck in review is patched to done ──────


@pytest.mark.asyncio
async def test_bss_s3_completed_analyze_patched_to_done(monkeypatch):
    """BSS-S3: review + completed + analyze + REQ tag → PATCHed to done."""
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)

    issues = {
        "proj-1": [
            _make_issue("iss-1", "review", "completed", ["analyze", "REQ-1"]),
        ]
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert ("proj-1", "iss-1", "done") in captured, (
        "BSS-S3: matching analyze issue MUST be PATCHed to statusId='done'"
    )
    assert result.get("patched") == 1, (
        f"BSS-S3: patched count MUST be 1; got {result!r}"
    )


# ─── BSS-S4: verifier issue is skipped ───────────────────────────────────────


@pytest.mark.asyncio
async def test_bss_s4_verifier_issue_skipped(monkeypatch):
    """BSS-S4: review + completed + verifier tag → NOT PATCHed (preserve escalate path)."""
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)

    issues = {
        "proj-1": [
            _make_issue("iss-v", "review", "completed", ["verifier", "REQ-1"]),
        ]
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert captured == [], (
        "BSS-S4: verifier issue MUST NOT be PATCHed"
    )
    assert result.get("patched") == 0, (
        f"BSS-S4: patched count MUST be 0; got {result!r}"
    )


# ─── BSS-S5: running session is skipped ──────────────────────────────────────


@pytest.mark.asyncio
async def test_bss_s5_running_session_skipped(monkeypatch):
    """BSS-S5: review + running + analyze + REQ tag → NOT PATCHed."""
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)

    issues = {
        "proj-1": [
            _make_issue("iss-run", "review", "running", ["analyze", "REQ-1"]),
        ]
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert captured == [], (
        "BSS-S5: running session issue MUST NOT be PATCHed"
    )
    assert result.get("patched") == 0, (
        f"BSS-S5: patched count MUST be 0; got {result!r}"
    )


# ─── BSS-S6: non-review status is skipped ────────────────────────────────────


@pytest.mark.asyncio
async def test_bss_s6_non_review_status_skipped(monkeypatch):
    """BSS-S6: done + completed + analyze + REQ tag → NOT PATCHed."""
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)

    issues = {
        "proj-1": [
            _make_issue("iss-done", "done", "completed", ["analyze", "REQ-1"]),
        ]
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert captured == [], (
        "BSS-S6: non-review status issue MUST NOT be PATCHed"
    )
    assert result.get("patched") == 0, (
        f"BSS-S6: patched count MUST be 0; got {result!r}"
    )


# ─── BSS-S7: issue without REQ tag is skipped ────────────────────────────────


@pytest.mark.asyncio
async def test_bss_s7_no_req_tag_skipped(monkeypatch):
    """BSS-S7: review + completed + analyze (no REQ tag) → NOT PATCHed."""
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)

    issues = {
        "proj-1": [
            _make_issue("iss-no-req", "review", "completed", ["analyze"]),
        ]
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert captured == [], (
        "BSS-S7: issue without REQ tag MUST NOT be PATCHed"
    )
    assert result.get("patched") == 0, (
        f"BSS-S7: patched count MUST be 0; got {result!r}"
    )


# ─── BSS-S8: individual PATCH failure does not abort batch ───────────────────


@pytest.mark.asyncio
async def test_bss_s8_individual_patch_failure_continues(monkeypatch):
    """BSS-S8: first PATCH fails with 500, second still PATCHed; report patched=1, failed=1."""
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)

    issues = {
        "proj-1": [
            _make_issue("iss-fail", "review", "completed", ["analyze", "REQ-1"]),
            _make_issue("iss-ok", "review", "completed", ["analyze", "REQ-1"]),
        ]
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues, patch_raises={"proj-1:iss-fail"})

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert ("proj-1", "iss-ok", "done") in captured, (
        "BSS-S8: second issue MUST still be PATCHed after first failure"
    )
    assert result.get("patched") == 1, (
        f"BSS-S8: patched count MUST be 1; got {result!r}"
    )
    assert result.get("failed") == 1, (
        f"BSS-S8: failed count MUST be 1; got {result!r}"
    )


# ─── BSS-S9: multiple projects are scanned ───────────────────────────────────


@pytest.mark.asyncio
async def test_bss_s9_multiple_projects_scanned(monkeypatch):
    """BSS-S9: active projects proj-a and proj-b each have one matching issue → both PATCHed."""
    from orchestrator import watchdog
    from orchestrator.store import db

    pool = _FakePool(rows=[
        {"project_id": "proj-a"},
        {"project_id": "proj-b"},
    ])
    _patch_pool(monkeypatch, pool)

    issues = {
        "proj-a": [
            _make_issue("iss-a", "review", "completed", ["analyze", "REQ-a"], project_id="proj-a"),
        ],
        "proj-b": [
            _make_issue("iss-b", "review", "completed", ["fixer", "REQ-b"], project_id="proj-b"),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert ("proj-a", "iss-a", "done") in captured, (
        "BSS-S9: proj-a issue MUST be PATCHed"
    )
    assert ("proj-b", "iss-b", "done") in captured, (
        "BSS-S9: proj-b issue MUST be PATCHed"
    )
    assert result.get("patched") == 2, (
        f"BSS-S9: patched count MUST be 2; got {result!r}"
    )
    assert result.get("failed") == 0, (
        f"BSS-S9: failed count MUST be 0; got {result!r}"
    )
