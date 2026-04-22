"""req_state CAS：placeholder 计数 / SQL 形态测试。

不连真 PG，monkeypatch fetchrow 捕 (sql, *args) 验签。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from orchestrator.state import Event, ReqState
from orchestrator.store import req_state as rs


class CapturePool:
    def __init__(self, ret=None):
        self.calls: list = []
        self._ret = ret or {"req_id": "REQ-1"}

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self._ret


@pytest.mark.asyncio
async def test_cas_no_context_patch_4_args():
    """无 patch → 5 个 placeholder 都不能在 SQL 里出现，args 必须 4 个。"""
    pool = CapturePool()
    ok = await rs.cas_transition(
        pool, "REQ-1", ReqState.INIT, ReqState.ANALYZING,
        Event.INTENT_ANALYZE, "start_analyze",
    )
    assert ok is True
    assert len(pool.calls) == 1
    sql, args = pool.calls[0]
    assert "$5" not in sql           # 不许漏 $5 placeholder
    assert "context = context" in sql
    assert len(args) == 4            # req_id, expected, next, history_json
    assert args[0] == "REQ-1"
    assert args[1] == "init"
    assert args[2] == "analyzing"
    history = json.loads(args[3])
    assert history[0]["event"] == "intent.analyze"


@pytest.mark.asyncio
async def test_cas_with_context_patch_5_args():
    pool = CapturePool()
    ok = await rs.cas_transition(
        pool, "REQ-1", ReqState.INIT, ReqState.ANALYZING,
        Event.INTENT_ANALYZE, "start_analyze",
        context_patch={"intent_issue_id": "i-9"},
    )
    assert ok is True
    sql, args = pool.calls[0]
    assert "$5::jsonb" in sql
    assert len(args) == 5
    assert json.loads(args[4]) == {"intent_issue_id": "i-9"}


@pytest.mark.asyncio
async def test_cas_failed_returns_false():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)   # CAS 没命中
    ok = await rs.cas_transition(
        pool, "REQ-1", ReqState.INIT, ReqState.ANALYZING,
        Event.INTENT_ANALYZE, "start_analyze",
    )
    assert ok is False


# ── M9: action_retries CRUD ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bump_action_retry_returns_new_round():
    pool = CapturePool(ret={"new_round": 3})
    r = await rs.bump_action_retry(pool, "REQ-1", "start_analyze")
    assert r == 3
    sql, args = pool.calls[0]
    assert "action_retries" in sql
    assert "jsonb_set" in sql
    assert "RETURNING" in sql
    assert args == ("REQ-1", "start_analyze")


@pytest.mark.asyncio
async def test_bump_action_retry_row_missing_returns_zero():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)   # row 不存在
    r = await rs.bump_action_retry(pool, "REQ-missing", "start_analyze")
    assert r == 0


@pytest.mark.asyncio
async def test_reset_action_retry_runs_subtract_sql():
    pool = AsyncMock()
    pool.execute = AsyncMock()
    await rs.reset_action_retry(pool, "REQ-1", "start_analyze")
    pool.execute.assert_awaited_once()
    sql, *args = pool.execute.call_args.args
    assert "action_retries" in sql
    assert "- $2::text" in sql
    assert args == ["REQ-1", "start_analyze"]


@pytest.mark.asyncio
async def test_get_action_retry_zero_when_missing():
    pool = CapturePool(ret={"r": None})
    r = await rs.get_action_retry(pool, "REQ-1", "create_dev")
    assert r == 0


@pytest.mark.asyncio
async def test_get_action_retry_returns_int():
    pool = CapturePool(ret={"r": 2})
    r = await rs.get_action_retry(pool, "REQ-1", "start_analyze")
    assert r == 2
