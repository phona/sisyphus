"""stage_runs store helper：不接真 PG，捕 (sql, args) 验签。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orchestrator.store import stage_runs as sr


class CapturePool:
    def __init__(self, ret: dict | None = None):
        self.fetchrow_calls: list = []
        self.execute_calls: list = []
        self._ret = ret

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return self._ret

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))


@pytest.mark.asyncio
async def test_insert_stage_run_returns_id_and_binds_all_fields():
    pool = CapturePool(ret={"id": 42})
    started = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)

    run_id = await sr.insert_stage_run(
        pool,
        "REQ-7",
        "dev",
        parallel_id="p-1",
        agent_type="coder",
        model="opus-4-7",
        started_at=started,
    )

    assert run_id == 42
    assert len(pool.fetchrow_calls) == 1
    sql, args = pool.fetchrow_calls[0]
    assert "INSERT INTO stage_runs" in sql
    assert "RETURNING id" in sql
    assert args == ("REQ-7", "dev", "p-1", "coder", "opus-4-7", started)


@pytest.mark.asyncio
async def test_insert_stage_run_defaults_started_at_to_now():
    pool = CapturePool(ret={"id": 1})
    before = datetime.now(UTC)
    await sr.insert_stage_run(pool, "REQ-1", "analyze")
    after = datetime.now(UTC)

    _, args = pool.fetchrow_calls[0]
    # 最后一位是 started_at，应落在 [before, after] 内
    assert before <= args[5] <= after
    assert args[2] is None  # parallel_id
    assert args[3] is None  # agent_type
    assert args[4] is None  # model


@pytest.mark.asyncio
async def test_update_stage_run_fills_outcome_and_tokens():
    pool = CapturePool()
    ended = datetime(2026, 4, 23, 10, 5, tzinfo=UTC)

    await sr.update_stage_run(
        pool,
        42,
        outcome="pass",
        token_in=1234,
        token_out=567,
        ended_at=ended,
    )

    assert len(pool.execute_calls) == 1
    sql, args = pool.execute_calls[0]
    assert "UPDATE stage_runs" in sql
    assert "duration_sec" in sql
    assert args == (42, ended, "pass", None, 1234, 567)


@pytest.mark.asyncio
async def test_update_stage_run_defaults_ended_at_to_now():
    pool = CapturePool()
    before = datetime.now(UTC)
    await sr.update_stage_run(pool, 1, outcome="fail", fail_reason="timeout")
    after = datetime.now(UTC)

    _, args = pool.execute_calls[0]
    assert args[0] == 1
    assert before <= args[1] <= after
    assert args[2] == "fail"
    assert args[3] == "timeout"


@pytest.mark.asyncio
async def test_update_stage_run_allows_partial_update():
    """仅补 token，不覆盖已有 outcome（COALESCE 由 SQL 保证）。"""
    pool = CapturePool()
    await sr.update_stage_run(pool, 7, token_in=100)

    _, args = pool.execute_calls[0]
    assert args[0] == 7
    assert args[2] is None   # outcome 不动
    assert args[3] is None   # fail_reason 不动
    assert args[4] == 100
    assert args[5] is None
