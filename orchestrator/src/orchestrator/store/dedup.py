"""Event ID dedup via Postgres unique key.

event_id 由 webhook handler 算（参考 router/router.js 里 dedup key 设计）：
  session.completed: timestamp|issueId|event|executionId
  issue.updated:     timestamp|issueId|event
"""
from __future__ import annotations

import asyncpg


async def check_and_record(pool: asyncpg.Pool, event_id: str) -> bool:
    """返回 True = 新事件（已记下）；False = 重复事件（需 skip）。

    INSERT ON CONFLICT DO NOTHING + RETURNING 实现原子 check + record。
    """
    row = await pool.fetchrow(
        "INSERT INTO event_seen(event_id) VALUES($1) "
        "ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
        event_id,
    )
    return row is not None
