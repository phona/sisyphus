"""backfill_outcomes_for_req 单测 + 集成测试（P0-1）。

单测：FakePool 验证 SQL 结构、参数、返回值解析正确。
集成测试：真 PG 验证 6 种 (decision_action, terminal_state) → actual_outcome 映射、
          幂等（第 2 次 0 行）、部分回填（已有值不被覆盖）。
"""
from __future__ import annotations

import os
import uuid

import pytest

from orchestrator.store.verifier_decisions import backfill_outcomes_for_req, insert_decision


# ── FakePool ────────────────────────────────────────────────────────────────

class _FakePool:
    """asyncpg pool stub：记录 execute 调用，返回可配置的 'UPDATE N' 字符串。"""

    def __init__(self, updated: int = 0):
        self._updated = updated
        self.execute_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))
        return f"UPDATE {self._updated}"


# ── 单元测试：SQL 结构 + 参数 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_passes_req_id_and_terminal_as_positional_params():
    """backfill_outcomes_for_req 必须将 req_id 作为 $1、terminal_state 作为 $2 传入。"""
    pool = _FakePool(updated=3)
    n = await backfill_outcomes_for_req(pool, "REQ-unit-001", "DONE")

    assert len(pool.execute_calls) == 1
    sql, args = pool.execute_calls[0]
    assert "UPDATE verifier_decisions" in sql
    assert args[0] == "REQ-unit-001"
    assert args[1] == "DONE"
    assert n == 3


@pytest.mark.asyncio
async def test_backfill_sql_contains_all_outcome_and_action_values():
    """SQL 必须覆盖全部 outcome 和 decision_action 枚举值，且只回填 NULL 行。"""
    pool = _FakePool()
    await backfill_outcomes_for_req(pool, "REQ-unit-002", "ESCALATED")

    sql, _ = pool.execute_calls[0]
    # outcome 枚举
    for outcome in ("'hit'", "'silent_pass'", "'fixer_failed'", "'over_cautious'"):
        assert outcome in sql, f"missing outcome: {outcome}"
    # decision_action 枚举
    for action in ("'pass'", "'fix'", "'escalate'"):
        assert action in sql, f"missing action: {action}"
    # 幂等 guard
    assert "actual_outcome IS NULL" in sql


@pytest.mark.asyncio
async def test_backfill_returns_zero_when_no_rows_updated():
    pool = _FakePool(updated=0)
    n = await backfill_outcomes_for_req(pool, "REQ-unit-003", "DONE")
    assert n == 0


@pytest.mark.asyncio
async def test_backfill_returns_count_from_execute_result():
    pool = _FakePool(updated=7)
    n = await backfill_outcomes_for_req(pool, "REQ-unit-004", "ESCALATED")
    assert n == 7


# ── 集成测试：真 PG ─────────────────────────────────────────────────────────

@pytest.mark.integration
class TestBackfillMappingIntegration:
    """6 種 mapping + 幂等 + 部分回填。需要 SISYPHUS_PG_DSN 指向真实 PostgreSQL。"""

    _dsn = os.environ.get("SISYPHUS_PG_DSN", "postgresql://test:test@localhost/test")

    async def _pool(self):
        import asyncpg
        return await asyncpg.create_pool(self._dsn)

    def _req(self) -> str:
        return f"REQ-backfill-integ-{uuid.uuid4()}"

    async def _fetch(self, pool, dec_id: int) -> dict:
        row = await pool.fetchrow(
            "SELECT actual_outcome, decision_correct FROM verifier_decisions WHERE id=$1",
            dec_id,
        )
        return dict(row)

    @pytest.mark.asyncio
    async def test_pass_done_maps_to_hit(self):
        pool = await self._pool()
        req_id = self._req()
        try:
            dec_id = await insert_decision(pool, req_id, "spec_lint", "check_fail",
                                           action="pass")
            n = await backfill_outcomes_for_req(pool, req_id, "DONE")
            assert n == 1
            row = await self._fetch(pool, dec_id)
            assert row["actual_outcome"] == "hit"
            assert row["decision_correct"] is True
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_pass_escalated_maps_to_silent_pass(self):
        pool = await self._pool()
        req_id = self._req()
        try:
            dec_id = await insert_decision(pool, req_id, "staging_test", "check_fail",
                                           action="pass")
            await backfill_outcomes_for_req(pool, req_id, "ESCALATED")
            row = await self._fetch(pool, dec_id)
            assert row["actual_outcome"] == "silent_pass"
            assert row["decision_correct"] is False
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_fix_done_maps_to_hit(self):
        pool = await self._pool()
        req_id = self._req()
        try:
            dec_id = await insert_decision(pool, req_id, "staging_test", "check_fail",
                                           action="fix", fixer="dev")
            await backfill_outcomes_for_req(pool, req_id, "DONE")
            row = await self._fetch(pool, dec_id)
            assert row["actual_outcome"] == "hit"
            assert row["decision_correct"] is True
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_fix_escalated_maps_to_fixer_failed(self):
        pool = await self._pool()
        req_id = self._req()
        try:
            dec_id = await insert_decision(pool, req_id, "staging_test", "check_fail",
                                           action="fix", fixer="dev")
            await backfill_outcomes_for_req(pool, req_id, "ESCALATED")
            row = await self._fetch(pool, dec_id)
            assert row["actual_outcome"] == "fixer_failed"
            assert row["decision_correct"] is False
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_escalate_done_maps_to_over_cautious(self):
        pool = await self._pool()
        req_id = self._req()
        try:
            dec_id = await insert_decision(pool, req_id, "pr_ci", "check_fail",
                                           action="escalate")
            await backfill_outcomes_for_req(pool, req_id, "DONE")
            row = await self._fetch(pool, dec_id)
            assert row["actual_outcome"] == "over_cautious"
            assert row["decision_correct"] is False
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_escalate_escalated_maps_to_hit(self):
        pool = await self._pool()
        req_id = self._req()
        try:
            dec_id = await insert_decision(pool, req_id, "accept", "check_fail",
                                           action="escalate")
            await backfill_outcomes_for_req(pool, req_id, "ESCALATED")
            row = await self._fetch(pool, dec_id)
            assert row["actual_outcome"] == "hit"
            assert row["decision_correct"] is True
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_idempotent_second_backfill_updates_zero_rows(self):
        """同一 req 跑 2 次 backfill，第 2 次更新 0 行（WHERE actual_outcome IS NULL）。"""
        pool = await self._pool()
        req_id = self._req()
        try:
            await insert_decision(pool, req_id, "staging_test", "check_fail", action="pass")
            n1 = await backfill_outcomes_for_req(pool, req_id, "DONE")
            n2 = await backfill_outcomes_for_req(pool, req_id, "DONE")
            assert n1 == 1
            assert n2 == 0
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()

    @pytest.mark.asyncio
    async def test_partial_backfill_does_not_overwrite_existing_outcome(self):
        """actual_outcome 已有值的行不被覆盖，NULL 行正常回填。"""
        pool = await self._pool()
        req_id = self._req()
        try:
            dec1 = await insert_decision(pool, req_id, "spec_lint", "check_fail",
                                         action="pass")
            await pool.execute(
                "UPDATE verifier_decisions SET actual_outcome='already_set',"
                " decision_correct=TRUE WHERE id=$1",
                dec1,
            )
            dec2 = await insert_decision(pool, req_id, "staging_test", "check_fail",
                                         action="fix")
            n = await backfill_outcomes_for_req(pool, req_id, "DONE")
            assert n == 1  # only dec2 was NULL

            row1 = await pool.fetchrow(
                "SELECT actual_outcome FROM verifier_decisions WHERE id=$1", dec1
            )
            row2 = await pool.fetchrow(
                "SELECT actual_outcome FROM verifier_decisions WHERE id=$1", dec2
            )
            assert row1["actual_outcome"] == "already_set"
            assert row2["actual_outcome"] == "hit"
        finally:
            await pool.execute("DELETE FROM verifier_decisions WHERE req_id=$1", req_id)
            await pool.close()
