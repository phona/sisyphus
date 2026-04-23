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


async def close_latest_stage_run(
    pool: asyncpg.Pool,
    req_id: str,
    stage: str,
    *,
    outcome: str,
    fail_reason: str | None = None,
) -> int | None:
    """关闭 (req_id, stage) 最新一条 ended_at IS NULL 的 stage_run。

    用于 engine 离开 *_RUNNING state 时自动收尾上一阶段，
    不需要 caller 持有 run_id。返回被关闭的 id；没找到则返 None。
    """
    row = await pool.fetchrow(
        """
        UPDATE stage_runs SET
            ended_at     = NOW(),
            outcome      = $3,
            fail_reason  = COALESCE($4, fail_reason),
            duration_sec = EXTRACT(EPOCH FROM (NOW() - started_at))::real
        WHERE id = (
            SELECT id FROM stage_runs
             WHERE req_id = $1 AND stage = $2 AND ended_at IS NULL
             ORDER BY started_at DESC
             LIMIT 1
        )
        RETURNING id
        """,
        req_id, stage, outcome, fail_reason,
    )
    return int(row["id"]) if row else None
