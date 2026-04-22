"""M4 round 计数持久化：把 retries[stage] 存进 req_state.context。

放在 retry/ 模块内而不是 store/req_state.py，保 M4 模块自治、不污染老 store API。
JSONB || 是浅合并会整体替换 retries 子 dict，所以用 jsonb_set + jsonb_build_object
原地合并单 key。
"""
from __future__ import annotations

import asyncpg


async def increment_round(pool: asyncpg.Pool, req_id: str, stage: str) -> int:
    """原子递增 ctx.retries[stage]，返回新 round 值（1-based）。

    Row 不存在返 0（上层自己校验）。stage 名任意字符串，通过参数绑定避免注入。
    """
    row = await pool.fetchrow(
        """
        UPDATE req_state
        SET context = jsonb_set(
            context,
            '{retries}',
            COALESCE(context->'retries', '{}'::jsonb) ||
            jsonb_build_object(
                $2::text,
                COALESCE((context#>>ARRAY['retries', $2])::int, 0) + 1
            ),
            true
        ),
        updated_at = now()
        WHERE req_id = $1
        RETURNING (context#>>ARRAY['retries', $2])::int AS new_round
        """,
        req_id, stage,
    )
    return int(row["new_round"]) if row else 0


async def reset_round(pool: asyncpg.Pool, req_id: str, stage: str) -> None:
    """清零某 stage 的 round（admission pass 后调）。key 不存在则 no-op。"""
    await pool.execute(
        """
        UPDATE req_state
        SET context = jsonb_set(
            context,
            '{retries}',
            COALESCE(context->'retries', '{}'::jsonb) - $2::text,
            true
        ),
        updated_at = now()
        WHERE req_id = $1
        """,
        req_id, stage,
    )


async def get_round(pool: asyncpg.Pool, req_id: str, stage: str) -> int:
    """当前 round 值（不存在返 0）。用于测试/观测，不影响决策。"""
    row = await pool.fetchrow(
        "SELECT (context#>>ARRAY['retries', $2])::int AS r FROM req_state WHERE req_id = $1",
        req_id, stage,
    )
    if row is None or row["r"] is None:
        return 0
    return int(row["r"])
