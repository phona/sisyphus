"""snapshot._flatten + sync_once 烟测。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from orchestrator import snapshot
from orchestrator.bkd import Issue


def make_issue(**kw):
    base = dict(
        id="i1", project_id="p", issue_number=1, title="t", status_id="working",
        tags=[], session_status=None, description=None,
        created_at="2026-04-21T01:00:00Z", updated_at="2026-04-21T01:05:00Z",
    )
    base.update(kw)
    return Issue(**base)


def test_flatten_basic():
    i = make_issue(id="dev-9", tags=["dev", "REQ-9"])
    row = snapshot._flatten(i)
    assert row["issue_id"] == "dev-9"
    assert row["req_id"] == "REQ-9"
    assert row["stage"] == "dev"
    assert row["round"] is None
    assert row["target"] is None


def test_flatten_ci_with_round():
    i = make_issue(id="ci-1", tags=["ci", "REQ-9", "target:integration", "parent:dev",
                                     "parent-id:dev-9", "round-2"])
    row = snapshot._flatten(i)
    assert row["stage"] == "ci"
    assert row["round"] == 2
    assert row["target"] == "integration"
    assert row["parent_stage"] == "dev"
    assert row["parent_issue_id"] == "dev-9"


def test_flatten_no_recognized_tags():
    i = make_issue(id="x", tags=["weird"])
    row = snapshot._flatten(i)
    assert row["stage"] is None
    assert row["req_id"] is None


@pytest.mark.asyncio
async def test_sync_once_noop_without_obs(monkeypatch):
    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: None)
    n = await snapshot.sync_once()
    assert n == 0


class FakeMainPool:
    """模拟 main pool 的 fetch SELECT DISTINCT project_id。"""
    def __init__(self, project_ids: list[str]):
        self._rows = [{"project_id": p} for p in project_ids]

    async def fetch(self, sql, *args):
        return self._rows


@pytest.mark.asyncio
async def test_sync_once_no_projects_yet(monkeypatch):
    """req_state 没记录时返 0，不报错。"""
    class _ObsPool:
        pass
    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: _ObsPool())
    monkeypatch.setattr(snapshot.db, "get_pool", lambda: FakeMainPool([]))
    n = await snapshot.sync_once()
    assert n == 0


@pytest.mark.asyncio
async def test_sync_once_upserts_per_project(monkeypatch):
    """两个 project 各 1 issue，应当 UPSERT 2 行。"""
    captured: list[tuple] = []

    class FakeConn:
        async def execute(self, sql, *args):
            captured.append((sql.strip()[:30], args))

        def transaction(self):
            @asynccontextmanager
            async def _t():
                yield
            return _t()

    class FakeObsPool:
        def acquire(self):
            @asynccontextmanager
            async def _a():
                yield FakeConn()
            return _a()

    fake_bkd = AsyncMock()
    # 每次 list_issues 返一条
    fake_bkd.list_issues = AsyncMock(side_effect=[
        [make_issue(id="a", tags=["dev", "REQ-1"])],
        [make_issue(id="b", tags=["accept", "REQ-2", "result:pass"])],
    ])

    @asynccontextmanager
    async def _client_ctx(*a, **kw):
        yield fake_bkd

    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: FakeObsPool())
    monkeypatch.setattr(snapshot.db, "get_pool", lambda: FakeMainPool(["proj-A", "proj-B"]))
    monkeypatch.setattr(snapshot, "BKDClient", _client_ctx)

    n = await snapshot.sync_once()
    assert n == 2
    assert fake_bkd.list_issues.await_count == 2  # 每个 project 一次
    assert len(captured) == 2
    assert all(sql.startswith("INSERT INTO bkd_snapshot") for sql, _ in captured)
