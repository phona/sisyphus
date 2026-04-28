"""table_ttl 单测 + 集成测试。

单测：mock pool，验证 SQL 语句和 cutoff 参数正确。
集成测试：真 PG，insert 过期行 + 新鲜行，验证只删过期行。
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from orchestrator.maintenance.table_ttl import run_loop, run_ttl_cleanup

# ── 测试用 config stub ─────────────────────────────────────────────────────────

class _Cfg:
    """最小化 settings stub，只含 TTL 天数字段。"""

    def __init__(
        self,
        ttl_event_seen_days: int = 30,
        ttl_dispatch_slugs_days: int = 90,
        ttl_verifier_decisions_days: int = 90,
        ttl_stage_runs_closed_days: int = 90,
    ):
        self.ttl_event_seen_days = ttl_event_seen_days
        self.ttl_dispatch_slugs_days = ttl_dispatch_slugs_days
        self.ttl_verifier_decisions_days = ttl_verifier_decisions_days
        self.ttl_stage_runs_closed_days = ttl_stage_runs_closed_days


# ── FakePool ─────────────────────────────────────────────────────────────────

class _FakePool:
    """asyncpg pool stub: 记录所有 execute/fetchval 调用，返回固定 count。"""

    def __init__(self, count: int = 100):
        self._count = count
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        self.fetchval_calls.append((sql, args))
        return self._count

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))


# ── 单元测试：SQL 结构 + cutoff 参数 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_seen_delete_sql_and_cutoff():
    """event_seen DELETE 用 seen_at < $1，cutoff ≈ 30 天前。"""
    pool = _FakePool()
    cfg = _Cfg(ttl_event_seen_days=30)
    before = datetime.now(UTC)
    await run_ttl_cleanup(pool, cfg)
    after = datetime.now(UTC)

    deletes = [(sql, args) for sql, args in pool.execute_calls if "event_seen" in sql]
    assert len(deletes) == 1
    sql, args = deletes[0]
    assert "DELETE FROM event_seen" in sql
    assert "seen_at < $1" in sql
    cutoff: datetime = args[0]
    assert (before - timedelta(days=30, seconds=1)) <= cutoff <= (after - timedelta(days=30) + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_dispatch_slugs_delete_sql_and_cutoff():
    """dispatch_slugs DELETE 用 created_at < $1，cutoff ≈ 90 天前。"""
    pool = _FakePool()
    cfg = _Cfg(ttl_dispatch_slugs_days=90)
    before = datetime.now(UTC)
    await run_ttl_cleanup(pool, cfg)
    after = datetime.now(UTC)

    deletes = [(sql, args) for sql, args in pool.execute_calls if "dispatch_slugs" in sql]
    assert len(deletes) == 1
    sql, args = deletes[0]
    assert "DELETE FROM dispatch_slugs" in sql
    assert "created_at < $1" in sql
    cutoff: datetime = args[0]
    assert (before - timedelta(days=90, seconds=1)) <= cutoff <= (after - timedelta(days=90) + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_verifier_decisions_delete_sql_and_cutoff():
    """verifier_decisions DELETE 用 made_at < $1，cutoff ≈ 90 天前。"""
    pool = _FakePool()
    cfg = _Cfg(ttl_verifier_decisions_days=90)
    before = datetime.now(UTC)
    await run_ttl_cleanup(pool, cfg)
    after = datetime.now(UTC)

    deletes = [(sql, args) for sql, args in pool.execute_calls if "verifier_decisions" in sql]
    assert len(deletes) == 1
    sql, args = deletes[0]
    assert "DELETE FROM verifier_decisions" in sql
    assert "made_at < $1" in sql
    cutoff: datetime = args[0]
    assert (before - timedelta(days=90, seconds=1)) <= cutoff <= (after - timedelta(days=90) + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_stage_runs_delete_only_closed():
    """stage_runs DELETE 必须带 ended_at IS NOT NULL 条件，不删 open 行。"""
    pool = _FakePool()
    cfg = _Cfg(ttl_stage_runs_closed_days=90)
    await run_ttl_cleanup(pool, cfg)

    deletes = [(sql, args) for sql, args in pool.execute_calls if "stage_runs" in sql]
    assert len(deletes) == 1
    sql, _args = deletes[0]
    assert "DELETE FROM stage_runs" in sql
    assert "ended_at IS NOT NULL" in sql
    assert "ended_at < $1" in sql


@pytest.mark.asyncio
async def test_summary_dict_contains_all_tables():
    """run_ttl_cleanup 返回包含 4 张表统计的 dict。"""
    pool = _FakePool(count=50)
    result = await run_ttl_cleanup(pool, _Cfg())

    for table in ("event_seen", "dispatch_slugs", "verifier_decisions", "stage_runs"):
        assert table in result
        assert "before" in result[table]
        assert "after" in result[table]
        assert "deleted" in result[table]
        assert result[table]["before"] == 50
        assert result[table]["after"] == 50
        assert result[table]["deleted"] == 0


@pytest.mark.asyncio
async def test_run_loop_exits_when_disabled(monkeypatch):
    """ttl_cleanup_enabled=False → run_loop 立刻返回（不进 while True）。"""
    from orchestrator.maintenance import table_ttl

    monkeypatch.setattr(table_ttl.settings, "ttl_cleanup_enabled", False)
    # 如果 run_loop 没有立刻退出会卡死（asyncio timeout 会 fail），
    # 但 pytest-asyncio auto mode 下直接 await 就行——返回意味着退出了。
    await run_loop()  # must return without blocking


# ── 集成测试：真 PG ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestTTLCleanupIntegration:
    """需要真实 PostgreSQL（由 SISYPHUS_PG_DSN 指定）。"""

    _dsn = os.environ.get("SISYPHUS_PG_DSN", "postgresql://test:test@localhost/test")

    async def _pool(self):
        import asyncpg
        return await asyncpg.create_pool(self._dsn)

    @pytest.mark.asyncio
    async def test_event_seen_deletes_old_keeps_recent(self):
        """event_seen: seen_at > 30 天的行被删，30 天内的保留。"""
        pool = await self._pool()
        old_id = f"ttl-test-{uuid.uuid4()}"
        recent_id = f"ttl-test-{uuid.uuid4()}"
        try:
            await pool.execute(
                "INSERT INTO event_seen (event_id, seen_at) VALUES ($1, $2)",
                old_id, datetime.now(UTC) - timedelta(days=40),
            )
            await pool.execute(
                "INSERT INTO event_seen (event_id, seen_at) VALUES ($1, $2)",
                recent_id, datetime.now(UTC) - timedelta(days=1),
            )
            await run_ttl_cleanup(pool, _Cfg(ttl_event_seen_days=30))

            assert await pool.fetchrow("SELECT 1 FROM event_seen WHERE event_id=$1", old_id) is None
            assert await pool.fetchrow("SELECT 1 FROM event_seen WHERE event_id=$1", recent_id) is not None
        finally:
            await pool.execute("DELETE FROM event_seen WHERE event_id LIKE 'ttl-test-%'")
            await pool.close()

    @pytest.mark.asyncio
    async def test_stage_runs_open_never_deleted(self):
        """stage_runs: ended_at IS NULL 的行永远不删（无论 ended_at cutoff）。"""
        pool = await self._pool()
        # ended_at IS NULL = still running（far past started_at，但没 ended_at）
        run_id: int = await pool.fetchval(
            """INSERT INTO stage_runs (req_id, stage, started_at)
               VALUES ($1, 'test-ttl', $2) RETURNING id""",
            f"REQ-ttl-test-{uuid.uuid4()}",
            datetime.now(UTC) - timedelta(days=180),
        )
        try:
            await run_ttl_cleanup(pool, _Cfg(ttl_stage_runs_closed_days=1))
            row = await pool.fetchrow("SELECT id FROM stage_runs WHERE id=$1", run_id)
            assert row is not None, "open stage_run (ended_at IS NULL) must never be deleted"
        finally:
            await pool.execute("DELETE FROM stage_runs WHERE id=$1", run_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_stage_runs_closed_old_deleted_recent_kept(self):
        """stage_runs: ended_at IS NOT NULL かつ > 90 日の行は削除、新しい行は保留。"""
        pool = await self._pool()
        req_pfx = f"REQ-ttl-test-{uuid.uuid4()}"
        old_run_id: int = await pool.fetchval(
            """INSERT INTO stage_runs (req_id, stage, started_at, ended_at)
               VALUES ($1, 'test-ttl', $2, $3) RETURNING id""",
            req_pfx + "-old",
            datetime.now(UTC) - timedelta(days=100),
            datetime.now(UTC) - timedelta(days=95),
        )
        recent_run_id: int = await pool.fetchval(
            """INSERT INTO stage_runs (req_id, stage, started_at, ended_at)
               VALUES ($1, 'test-ttl', $2, $3) RETURNING id""",
            req_pfx + "-recent",
            datetime.now(UTC) - timedelta(days=5),
            datetime.now(UTC) - timedelta(days=1),
        )
        try:
            await run_ttl_cleanup(pool, _Cfg(ttl_stage_runs_closed_days=90))
            assert await pool.fetchrow("SELECT id FROM stage_runs WHERE id=$1", old_run_id) is None
            assert await pool.fetchrow("SELECT id FROM stage_runs WHERE id=$1", recent_run_id) is not None
        finally:
            await pool.execute(
                "DELETE FROM stage_runs WHERE id = ANY($1::bigint[])",
                [old_run_id, recent_run_id],
            )
            await pool.close()
