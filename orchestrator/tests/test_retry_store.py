"""retry.store 单测：mock asyncpg Pool，验 SQL 参数 / row 解析。

真正的 JSONB 合并行为在集成测里（有真 pg 才能测）；这里只证调用 shape
+ row=None 的退化路径。
"""
from __future__ import annotations

import pytest

from orchestrator.retry import store as retry_store


class StubRow(dict):
    """既像 dict 也像 asyncpg.Record（都支持 ["key"] 访问）。"""


class FakePool:
    def __init__(self, fetchrow_return=None, execute_sink=None):
        self._fetchrow_return = fetchrow_return
        self._execute_sink = execute_sink if execute_sink is not None else []
        self.fetchrow_calls: list = []

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return self._fetchrow_return

    async def execute(self, sql, *args):
        self._execute_sink.append((sql, args))
        return None


# ─── increment_round ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_increment_round_returns_new_value():
    pool = FakePool(fetchrow_return=StubRow({"new_round": 3}))
    r = await retry_store.increment_round(pool, "REQ-1", "staging-test")
    assert r == 3
    assert len(pool.fetchrow_calls) == 1
    sql, args = pool.fetchrow_calls[0]
    assert args == ("REQ-1", "staging-test")
    assert "jsonb_set" in sql
    assert "retries" in sql


@pytest.mark.asyncio
async def test_increment_round_row_missing_returns_zero():
    """req_state 里没这个 req_id → row=None，返 0（不炸）。"""
    pool = FakePool(fetchrow_return=None)
    r = await retry_store.increment_round(pool, "REQ-nope", "staging-test")
    assert r == 0


# ─── reset_round ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reset_round_executes_delete():
    sink: list = []
    pool = FakePool(execute_sink=sink)
    await retry_store.reset_round(pool, "REQ-1", "staging-test")
    assert len(sink) == 1
    sql, args = sink[0]
    assert args == ("REQ-1", "staging-test")
    assert "jsonb_set" in sql
    # 用 - 操作符 drop key
    assert "- $2::text" in sql


# ─── get_round ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_round_returns_value():
    pool = FakePool(fetchrow_return=StubRow({"r": 2}))
    r = await retry_store.get_round(pool, "REQ-1", "staging-test")
    assert r == 2


@pytest.mark.asyncio
async def test_get_round_null_returns_zero():
    pool = FakePool(fetchrow_return=StubRow({"r": None}))
    r = await retry_store.get_round(pool, "REQ-1", "staging-test")
    assert r == 0


@pytest.mark.asyncio
async def test_get_round_no_row_returns_zero():
    pool = FakePool(fetchrow_return=None)
    r = await retry_store.get_round(pool, "REQ-nope", "staging-test")
    assert r == 0
