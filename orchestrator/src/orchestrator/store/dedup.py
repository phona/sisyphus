"""Event ID dedup via Postgres unique key.

event_id 由 webhook handler 算：
  session.completed: issueId|event|executionId
  issue.updated:     timestamp|issueId|event
"""
from __future__ import annotations

from typing import Literal

import asyncpg


async def check_and_record(
    pool: asyncpg.Pool, event_id: str
) -> Literal["new", "retry", "skip"]:
    """
    - "new"   = 全新事件，已 INSERT processed_at=NULL，handler 该跑
    - "retry" = 之前 INSERT 过但 processed_at IS NULL（上次崩溃），handler 该跑
    - "skip"  = 之前 INSERT 且 processed_at IS NOT NULL（已成功处理），handler 不跑
    """
    row = await pool.fetchrow(
        "INSERT INTO event_seen(event_id) VALUES($1) "
        "ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
        event_id,
    )
    if row is not None:
        return "new"
    existing = await pool.fetchrow(
        "SELECT processed_at FROM event_seen WHERE event_id = $1",
        event_id,
    )
    if existing and existing["processed_at"] is None:
        return "retry"
    return "skip"


async def mark_processed(pool: asyncpg.Pool, event_id: str) -> None:
    """handler 跑完成功时调，标 processed_at = NOW()。"""
    await pool.execute(
        "UPDATE event_seen SET processed_at = NOW() WHERE event_id = $1",
        event_id,
    )
