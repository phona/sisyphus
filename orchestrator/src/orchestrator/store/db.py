"""Postgres 连接池（asyncpg）。

两套池：
- 主池：req_state / event_seen（业务关键，dsn 必填）
- obs 池：observability schema（dsn 可空 → 完全关闭观测写入）
"""
from __future__ import annotations

import asyncpg

_pool: asyncpg.Pool | None = None
_obs_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10, command_timeout=10)
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call init_pool first")
    return _pool


async def init_obs_pool(dsn: str) -> asyncpg.Pool | None:
    """init obs pool；dsn 空字符串则跳过返 None。"""
    global _obs_pool
    if not dsn:
        return None
    if _obs_pool is None:
        _obs_pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4, command_timeout=5,  # tap 不能久阻塞
        )
    return _obs_pool


def get_obs_pool() -> asyncpg.Pool | None:
    """没初始化或显式关闭就返 None；调用方需 if obs: 再写。"""
    return _obs_pool


async def close_pool() -> None:
    global _pool, _obs_pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    if _obs_pool is not None:
        await _obs_pool.close()
        _obs_pool = None
