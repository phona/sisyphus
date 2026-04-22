"""artifact_checks 表写入 helper。"""
from __future__ import annotations

import asyncpg

from ..checkers.staging_test import CheckResult


async def insert_check(pool: asyncpg.Pool, req_id: str, stage: str, result: CheckResult) -> None:
    await pool.execute(
        """
        INSERT INTO artifact_checks
            (req_id, stage, passed, exit_code, cmd, stdout_tail, stderr_tail, duration_sec)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        req_id,
        stage,
        result.passed,
        result.exit_code,
        result.cmd,
        result.stdout_tail,
        result.stderr_tail,
        result.duration_sec,
    )
