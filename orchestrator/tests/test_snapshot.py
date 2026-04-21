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


@pytest.mark.asyncio
async def test_sync_once_upserts(monkeypatch):
    captured: list[tuple] = []

    class FakeConn:
        async def execute(self, sql, *args):
            captured.append((sql.strip()[:30], args))

        def transaction(self):
            @asynccontextmanager
            async def _t():
                yield
            return _t()

    class FakePool:
        def acquire(self):
            @asynccontextmanager
            async def _a():
                yield FakeConn()
            return _a()

    fake_bkd = AsyncMock()
    fake_bkd.list_issues = AsyncMock(return_value=[
        make_issue(id="a", tags=["dev", "REQ-1"]),
        make_issue(id="b", tags=["accept", "REQ-1", "result:pass"]),
    ])

    @asynccontextmanager
    async def _client_ctx(*a, **kw):
        yield fake_bkd

    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _client_ctx)
    # project_repo_map_json 默认有一个 project，sync_once 会用它
    n = await snapshot.sync_once()
    assert n == 2
    # 两次 UPSERT
    assert len(captured) == 2
    assert all(sql.startswith("INSERT INTO bkd_snapshot") for sql, _ in captured)
