"""alerts 表写入 helper。"""
from __future__ import annotations

import asyncpg


async def insert_alert(
    pool: asyncpg.Pool,
    *,
    severity: str,
    reason: str,
    hint: str | None = None,
    suggested_action: str | None = None,
    req_id: str | None = None,
    stage: str | None = None,
) -> int:
    """写一条 alert 行。返回 id。"""
    row = await pool.fetchrow(
        """
        INSERT INTO alerts(severity, req_id, stage, reason, hint, suggested_action)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        severity, req_id, stage, reason, hint, suggested_action,
    )
    return row["id"]


async def mark_sent_to_tg(pool: asyncpg.Pool, alert_id: int) -> None:
    await pool.execute(
        "UPDATE alerts SET sent_to_tg = TRUE WHERE id = $1", alert_id,
    )
