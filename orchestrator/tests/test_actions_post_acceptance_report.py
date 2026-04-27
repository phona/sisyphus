"""Tests for post_acceptance_report action.

REQ-bkd-acceptance-feedback-loop-1777278984 — covers spec scenarios USER-S9..S11
in openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/specs/user-acceptance-gate/spec.md
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest


@dataclass
class FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "working"
    tags: list = field(default_factory=list)
    session_status: str | None = None
    description: str | None = None


def _make_fake_bkd(*, description: str | None = None) -> AsyncMock:
    issue = FakeIssue(id="intent-7", description=description)
    bkd = AsyncMock()
    bkd.get_issue = AsyncMock(return_value=issue)
    bkd.update_issue = AsyncMock(return_value=issue)
    return bkd


def _patch_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*_a, **_kw):
        yield fake

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.BKDClient", _ctx,
    )


def _patch_db(monkeypatch):
    captured: list = []

    class P:
        async def execute(self, sql, *args):
            captured.append((sql.strip()[:60], args))

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.db.get_pool",
        lambda: P(),
    )
    return captured


def _make_body(issue_id="src-x", project_id="p"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id, "event": "issue.updated",
        "title": "", "tags": [], "issueNumber": None,
    })()


# ── USER-S9: first invocation patches description with managed block ──────────


@pytest.mark.asyncio
async def test_USER_S9_description_patch_with_marker_and_pr_url(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_fake_bkd(description="existing body\n")
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    ctx = {
        "intent_issue_id": "intent-7",
        "pr_urls": {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/200"},
    }
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx=ctx,
    )

    assert out["acceptance_reported"] is True
    assert out["bkd_ok"] is True

    # update_issue called once on the intent issue with description containing marker
    bkd.update_issue.assert_awaited_once()
    call_args, call_kwargs = bkd.update_issue.call_args
    assert call_args[0] == "p"
    assert call_args[1] == "intent-7"
    desc = call_kwargs["description"]
    assert mod.ACCEPTANCE_MARKER in desc
    assert "https://github.com/phona/sisyphus/pull/200" in desc
    # MUST NOT include statusId or tags in the PATCH
    assert "status_id" not in call_kwargs
    assert "tags" not in call_kwargs


# ── USER-S10: rerun replaces block, marker appears exactly once ───────────────


@pytest.mark.asyncio
async def test_USER_S10_rerun_replaces_block_idempotent(monkeypatch):
    """ACCEPTANCE_MARKER already in description → replaced, not appended."""
    from orchestrator.actions import post_acceptance_report as mod

    existing = "preamble\n" + mod.ACCEPTANCE_MARKER + "\nold block content\n"
    bkd = _make_fake_bkd(description=existing)
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    ctx = {"intent_issue_id": "intent-7", "pr_urls": {}}
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx=ctx,
    )

    assert out["acceptance_reported"] is True
    bkd.update_issue.assert_awaited_once()
    _, kwargs = bkd.update_issue.call_args
    desc = kwargs["description"]
    # marker appears exactly once (replaced, not appended)
    assert desc.count(mod.ACCEPTANCE_MARKER) == 1


# ── USER-S11: missing intent_issue_id → noop, no BKD calls ──────────────────


@pytest.mark.asyncio
async def test_USER_S11_missing_intent_issue_id_no_bkd_calls(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx={},
    )

    assert out["acceptance_reported"] is False
    assert "no intent_issue_id" in out["reason"]
    bkd.get_issue.assert_not_called()
    bkd.update_issue.assert_not_called()


# ── BKD failure does not propagate; bkd_ok=False signals it ─────────────────


@pytest.mark.asyncio
async def test_post_acceptance_report_bkd_failure_logged_not_raised(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_fake_bkd()
    bkd.update_issue.side_effect = RuntimeError("BKD 503")
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"],
        ctx={"intent_issue_id": "intent-7", "pr_urls": {}},
    )

    # action returns normally (doesn't raise); bkd_ok=False signals failure
    assert out["acceptance_reported"] is True
    assert out["bkd_ok"] is False
