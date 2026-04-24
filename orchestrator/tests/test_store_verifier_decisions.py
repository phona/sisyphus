"""verifier_decisions store helper：捕 (sql, args) 验签。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orchestrator.store import verifier_decisions as vd


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
async def test_insert_decision_full_fields_returns_id():
    pool = CapturePool(ret={"id": 13})
    made = datetime(2026, 4, 23, 11, 0, tzinfo=UTC)

    dec_id = await vd.insert_decision(
        pool,
        "REQ-3",
        "verify",
        "check_fail",
        action="fix",
        fixer="coder",
        scope="file",
        reason="lint failure on foo.py",
        confidence="high",
        made_at=made,
    )

    assert dec_id == 13
    assert len(pool.fetchrow_calls) == 1
    sql, args = pool.fetchrow_calls[0]
    assert "INSERT INTO verifier_decisions" in sql
    assert "RETURNING id" in sql
    # audit 列新增：最后一个参数为 None（调用时未传 audit）
    assert args == (
        "REQ-3", "verify", "check_fail",
        "fix", "coder", "file",
        "lint failure on foo.py", "high", made,
        None,
    )


@pytest.mark.asyncio
async def test_insert_decision_defaults_made_at_to_now_and_nulls():
    pool = CapturePool(ret={"id": 1})
    before = datetime.now(UTC)
    await vd.insert_decision(pool, "REQ-1", "verify", "check_fail")
    after = datetime.now(UTC)

    _, args = pool.fetchrow_calls[0]
    assert args[0] == "REQ-1"
    assert args[1] == "verify"
    assert args[2] == "check_fail"
    # action, fixer, scope, reason, confidence 全为 None
    assert args[3:8] == (None, None, None, None, None)
    assert before <= args[8] <= after


@pytest.mark.asyncio
async def test_mark_correct_writes_outcome_and_bool():
    pool = CapturePool()
    await vd.mark_correct(pool, 42, actual_outcome="pass", correct=True)

    assert len(pool.execute_calls) == 1
    sql, args = pool.execute_calls[0]
    assert "UPDATE verifier_decisions" in sql
    assert "actual_outcome" in sql
    assert "decision_correct" in sql
    assert args == (42, "pass", True)


@pytest.mark.asyncio
async def test_mark_correct_handles_false_outcome():
    pool = CapturePool()
    await vd.mark_correct(pool, 7, actual_outcome="fail", correct=False)

    _, args = pool.execute_calls[0]
    assert args == (7, "fail", False)


# ─── audit 字段（fixer-audit REQ）─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insert_decision_with_audit_serializes_json():
    """insert with audit：audit dict 被 json.dumps 序列化后传入 SQL 参数。"""
    import json
    pool = CapturePool(ret={"id": 99})
    audit = {
        "diff_summary": "src=+5/-2 tests=+3/-0",
        "verdict": "legitimate",
        "red_flags": [],
        "files_by_category": {"src": 2, "tests": 1, "spec": 0, "config": 0},
    }

    dec_id = await vd.insert_decision(
        pool,
        "REQ-audit-1",
        "staging_test",
        "success",
        action="pass",
        fixer=None,
        scope=None,
        reason="fix looks good",
        confidence="high",
        audit=audit,
    )

    assert dec_id == 99
    assert len(pool.fetchrow_calls) == 1
    sql, args = pool.fetchrow_calls[0]
    assert "audit" in sql
    # audit 被序列化成 JSON 字符串（asyncpg JSONB 不注册 type codec，传 str）
    audit_arg = args[-1]
    assert audit_arg is not None
    parsed = json.loads(audit_arg)
    assert parsed["verdict"] == "legitimate"
    assert parsed["files_by_category"]["src"] == 2


@pytest.mark.asyncio
async def test_insert_decision_without_audit_writes_null():
    """insert without audit：audit 参数默认为 None，SQL 参数传 None（向后兼容）。"""
    pool = CapturePool(ret={"id": 1})
    await vd.insert_decision(pool, "REQ-1", "staging_test", "success")

    _, args = pool.fetchrow_calls[0]
    # 最后一个参数是 audit，应为 None
    assert args[-1] is None
