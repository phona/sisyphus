"""watchdog BKD 补偿清理测试：_sync_stuck_sub_agent_statuses_tick。

REQ-fix-bkd-sub-issue-status-sync-1777426309
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from orchestrator import watchdog
from orchestrator.bkd import Issue


# ─── Fake pool ───────────────────────────────────────────────────────────
@dataclass
class FakePool:
    rows: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        return self.rows


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


# ─── Fake BKD ────────────────────────────────────────────────────────────
def _patch_bkd_for_sync(monkeypatch, issues_by_project: dict[str, list[Issue]],
                        patch_raises: set[str] | None = None):
    """mock BKDClient：list_issues 按 project_id 返回；update_issue 可选抛异常。"""
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
            # 返回 dummy issue
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


def _make_issue(issue_id: str, status_id: str, session_status: str, tags: list[str]) -> Issue:
    return Issue(
        id=issue_id, project_id="proj-1", issue_number=0,
        title="", status_id=status_id, tags=tags,
        session_status=session_status,
    )


# ─── Case 1：review + completed + execute tag + REQ tag → patched ─────────
@pytest.mark.asyncio
async def test_patches_stuck_execute_issue(monkeypatch):
    pool = FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-1": [
            _make_issue("iss-a", "review", "completed", ["execute", "REQ-1"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 1, "failed": 0}
    assert captured == [("proj-1", "iss-a", "done")]


# ─── Case 2：verifier tag → 跳过（保守策略，避免误动 escalate verifier）──
@pytest.mark.asyncio
async def test_skips_verifier_issue(monkeypatch):
    pool = FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-1": [
            _make_issue("iss-v", "review", "completed", ["verifier", "REQ-1"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 0, "failed": 0}
    assert captured == []


# ─── Case 3：sessionStatus=running → 跳过 ─────────────────────────────────
@pytest.mark.asyncio
async def test_skips_running_session(monkeypatch):
    pool = FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-1": [
            _make_issue("iss-a", "review", "running", ["execute", "REQ-1"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 0, "failed": 0}
    assert captured == []


# ─── Case 4：statusId != review → 跳过 ────────────────────────────────────
@pytest.mark.asyncio
async def test_skips_non_review_status(monkeypatch):
    pool = FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-1": [
            _make_issue("iss-a", "done", "completed", ["execute", "REQ-1"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 0, "failed": 0}
    assert captured == []


# ─── Case 5：无 REQ tag → 跳过 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_skips_issue_without_req_tag(monkeypatch):
    pool = FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-1": [
            _make_issue("iss-a", "review", "completed", ["execute"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 0, "failed": 0}
    assert captured == []


# ─── Case 6：无 role tag → 跳过 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_skips_issue_without_role_tag(monkeypatch):
    pool = FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-1": [
            _make_issue("iss-a", "review", "completed", ["REQ-1", "some-tag"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 0, "failed": 0}
    assert captured == []


# ─── Case 7：PATCH 失败 → 计数 failed，不抛异常 ───────────────────────────
@pytest.mark.asyncio
async def test_counts_patch_failure(monkeypatch):
    pool = FakePool(rows=[{"project_id": "proj-1"}])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-1": [
            _make_issue("iss-a", "review", "completed", ["execute", "REQ-1"]),
            _make_issue("iss-b", "review", "completed", ["fixer", "REQ-2"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues, patch_raises={"proj-1:iss-a"})

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 1, "failed": 1}
    assert captured == [("proj-1", "iss-b", "done")]


# ─── Case 8：多 project ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scans_multiple_projects(monkeypatch):
    pool = FakePool(rows=[
        {"project_id": "proj-a"},
        {"project_id": "proj-b"},
    ])
    _patch_pool(monkeypatch, pool)
    issues = {
        "proj-a": [
            _make_issue("iss-a1", "review", "completed", ["execute", "REQ-A1"]),
        ],
        "proj-b": [
            _make_issue("iss-b1", "review", "completed", ["challenger", "REQ-B1"]),
        ],
    }
    captured = _patch_bkd_for_sync(monkeypatch, issues)

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 2, "failed": 0}
    assert ("proj-a", "iss-a1", "done") in captured
    assert ("proj-b", "iss-b1", "done") in captured


# ─── Case 9：空 project rows → 无事发生 ───────────────────────────────────
@pytest.mark.asyncio
async def test_empty_projects_does_nothing(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    captured = _patch_bkd_for_sync(monkeypatch, {})

    result = await watchdog._sync_stuck_sub_agent_statuses_tick()

    assert result == {"patched": 0, "failed": 0}
    assert captured == []
