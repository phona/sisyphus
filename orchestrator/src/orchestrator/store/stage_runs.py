"""stage_runs 表写入 helper（M14e）。

典型用法：
    run_id = await insert_stage_run(pool, req_id, stage, agent_type=..., model=...)
    ...
    await update_stage_run(pool, run_id, outcome="pass", token_in=..., token_out=...)

埋点点由调用方自选；未埋点的 stage 不影响主流程。
"""
from __future__ import annotations

from datetime import UTC, datetime

import asyncpg


async def insert_stage_run(
    pool: asyncpg.Pool,
    req_id: str,
    stage: str,
    *,
    parallel_id: str | None = None,
    agent_type: str | None = None,
    model: str | None = None,
    started_at: datetime | None = None,
) -> int:
    """开一条 stage_run，返回自增 id。started_at 默认 now()。"""
    row = await pool.fetchrow(
        """
        INSERT INTO stage_runs
            (req_id, stage, parallel_id, agent_type, model, started_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        req_id,
        stage,
        parallel_id,
        agent_type,
        model,
        started_at or datetime.now(UTC),
    )
    return int(row["id"])


async def update_stage_run(
    pool: asyncpg.Pool,
    run_id: int,
    *,
    outcome: str | None = None,
    fail_reason: str | None = None,
    token_in: int | None = None,
    token_out: int | None = None,
    ended_at: datetime | None = None,
) -> None:
    """收尾 stage_run。ended_at 默认 now()；duration_sec 自动算。

    所有字段可选：同一行允许多次 update（token 后回填等）。
    """
    ended = ended_at or datetime.now(UTC)
    await pool.execute(
        """
        UPDATE stage_runs SET
            ended_at     = COALESCE($2, ended_at),
            outcome      = COALESCE($3, outcome),
            fail_reason  = COALESCE($4, fail_reason),
            token_in     = COALESCE($5, token_in),
            token_out    = COALESCE($6, token_out),
            duration_sec = COALESCE(
                EXTRACT(EPOCH FROM (COALESCE($2, ended_at) - started_at))::real,
                duration_sec
            )
        WHERE id = $1
        """,
        run_id,
        ended,
        outcome,
        fail_reason,
        token_in,
        token_out,
    )
