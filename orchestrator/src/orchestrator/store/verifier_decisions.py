"""verifier_decisions 表写入 helper（M14e）。

用法：
    dec_id = await insert_decision(pool, req_id, stage, trigger="check_fail",
                                   action="fix", fixer="coder", ...)
    # 等 verifier 判决的后果明朗后
    await mark_correct(pool, dec_id, actual_outcome="pass", correct=True)
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg


async def insert_decision(
    pool: asyncpg.Pool,
    req_id: str,
    stage: str,
    trigger: str,
    *,
    action: str | None = None,
    fixer: str | None = None,
    scope: str | None = None,
    reason: str | None = None,
    confidence: str | None = None,
    made_at: datetime | None = None,
    audit: dict | None = None,
) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO verifier_decisions
            (req_id, stage, trigger,
             decision_action, decision_fixer, decision_scope,
             decision_reason, decision_confidence, made_at, audit)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        req_id,
        stage,
        trigger,
        action,
        fixer,
        scope,
        reason,
        confidence,
        made_at or datetime.now(UTC),
        json.dumps(audit) if audit is not None else None,
    )
    return int(row["id"])


async def mark_correct(
    pool: asyncpg.Pool,
    decision_id: int,
    *,
    actual_outcome: str,
    correct: bool,
) -> None:
    """回填判决是否 correct；actual_outcome 是客观结果（pass/fail/cancelled）。"""
    await pool.execute(
        """
        UPDATE verifier_decisions SET
            actual_outcome   = $2,
            decision_correct = $3
        WHERE id = $1
        """,
        decision_id,
        actual_outcome,
        correct,
    )


async def backfill_outcomes_for_req(
    pool: asyncpg.Pool,
    req_id: str,
    terminal_state: str,
) -> int:
    """REQ 到终态时批量回填该 req_id 所有未标 actual_outcome 的 verifier 判决。

    terminal_state: "DONE" 或 "ESCALATED"（ReqState.value）。

    映射（P0-1）：
      pass/fix  + DONE      → hit         (correct=True)
      pass      + ESCALATED → silent_pass  (correct=False)
      fix       + ESCALATED → fixer_failed (correct=False)
      escalate  + DONE      → over_cautious(correct=False)
      escalate  + ESCALATED → hit          (correct=True)

    返回更新行数；异常由调用方处理。
    """
    result = await pool.execute(
        """
        UPDATE verifier_decisions SET
            actual_outcome = CASE
                WHEN decision_action IN ('pass', 'fix') AND $2 = 'DONE'
                    THEN 'hit'
                WHEN decision_action = 'pass'  AND $2 = 'ESCALATED'
                    THEN 'silent_pass'
                WHEN decision_action = 'fix'   AND $2 = 'ESCALATED'
                    THEN 'fixer_failed'
                WHEN decision_action = 'escalate' AND $2 = 'DONE'
                    THEN 'over_cautious'
                WHEN decision_action = 'escalate' AND $2 = 'ESCALATED'
                    THEN 'hit'
                ELSE actual_outcome
            END,
            decision_correct = CASE
                WHEN decision_action IN ('pass', 'fix') AND $2 = 'DONE'
                    THEN TRUE
                WHEN decision_action = 'pass'  AND $2 = 'ESCALATED'
                    THEN FALSE
                WHEN decision_action = 'fix'   AND $2 = 'ESCALATED'
                    THEN FALSE
                WHEN decision_action = 'escalate' AND $2 = 'DONE'
                    THEN FALSE
                WHEN decision_action = 'escalate' AND $2 = 'ESCALATED'
                    THEN TRUE
                ELSE decision_correct
            END
        WHERE req_id = $1
          AND actual_outcome IS NULL
          AND decision_action IN ('pass', 'fix', 'escalate')
        """,
        req_id,
        terminal_state,
    )
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return -1
