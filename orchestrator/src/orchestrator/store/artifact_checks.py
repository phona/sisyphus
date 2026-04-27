"""artifact_checks 表写入 helper。"""
from __future__ import annotations

import asyncpg

from ..checkers._types import CheckResult


async def insert_check(pool: asyncpg.Pool, req_id: str, stage: str, result: CheckResult) -> None:
    """写一条 checker 跑的结果。

    REQ-checker-infra-flake-retry-1777247423：新增 attempts + flake_reason 列。
    attempts ≥1（无 retry = 1）；flake_reason 仅在确实发生 retry 时非 NULL，
    取值 "flake-retry-recovered:<tag>" / "flake-retry-exhausted:<tag>"，
    其它 reason 字符串（如 "timeout"）也共用此列（informational）。
    """
    await pool.execute(
        """
        INSERT INTO artifact_checks
            (req_id, stage, passed, exit_code, cmd, stdout_tail, stderr_tail,
             duration_sec, attempts, flake_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        req_id,
        stage,
        result.passed,
        result.exit_code,
        result.cmd,
        result.stdout_tail,
        result.stderr_tail,
        result.duration_sec,
        result.attempts,
        result.reason,
    )
