"""baseline_results 表：staging_test baseline 缓存读写。

cache_key = "baseline:staging_test:<main_head_sha>"
TTL = 24h（查询时过滤 created_at > NOW() - 24h）
repo_results JSONB = {"repo-basename": true/false}

obs pool 是可选的（dsn 留空则 None），调用方负责 if obs_pool: 判断。
"""
from __future__ import annotations

import json

import asyncpg

_TTL_SQL = "INTERVAL '24 hours'"


async def get_cached(
    pool: asyncpg.Pool,
    cache_key: str,
) -> dict[str, bool] | None:
    """读 24h 内的 baseline 缓存。不命中返 None。"""
    row = await pool.fetchrow(
        f"""
        SELECT repo_results FROM baseline_results
        WHERE cache_key = $1
          AND created_at > NOW() - {_TTL_SQL}
        """,
        cache_key,
    )
    if row is None:
        return None
    return {k: bool(v) for k, v in row["repo_results"].items()}


async def put_cached(
    pool: asyncpg.Pool,
    cache_key: str,
    main_sha: str,
    repo_results: dict[str, bool],
) -> None:
    """写（或覆盖）baseline 缓存。UPSERT by cache_key。"""
    await pool.execute(
        """
        INSERT INTO baseline_results (cache_key, main_sha, repo_results)
        VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (cache_key) DO UPDATE SET
            main_sha     = EXCLUDED.main_sha,
            repo_results = EXCLUDED.repo_results,
            created_at   = NOW()
        """,
        cache_key,
        main_sha,
        json.dumps(repo_results),
    )
