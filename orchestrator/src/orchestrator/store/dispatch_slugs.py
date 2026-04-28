"""dispatch_slugs: slug → BKD issue_id 幂等映射。

action handler 在调用 bkd.create_issue() 之前先查本表：
- 命中 → 直接返回已有 issue_id，跳过 POST
- 未命中 → 创建 issue 后写入（INSERT ON CONFLICT DO NOTHING）

防止 webhook retry（dedup=retry）时 create_issue 被执行两次。
"""
from __future__ import annotations

import asyncpg


async def get(pool: asyncpg.Pool, slug: str) -> str | None:
    """返回已有 issue_id；slug 不存在时返 None。"""
    row = await pool.fetchrow(
        "SELECT issue_id FROM dispatch_slugs WHERE slug = $1",
        slug,
    )
    return row["issue_id"] if row else None


async def put(pool: asyncpg.Pool, slug: str, issue_id: str) -> None:
    """写入 slug → issue_id；slug 已存在时静默跳过（幂等）。"""
    await pool.execute(
        "INSERT INTO dispatch_slugs(slug, issue_id) VALUES($1, $2)"
        " ON CONFLICT DO NOTHING",
        slug,
        issue_id,
    )
