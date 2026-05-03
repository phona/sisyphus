"""BKD turn-level data collector — REQ-feat-agent-turns-collector-1777796671.

每 agent_turns_collector_interval_sec（默认 300s = 5min）扫一次：
- 从 stage_runs 取最近 24h 内关闭、且 bkd_issue_id IS NOT NULL 的行
- 对每个 (project_id, bkd_issue_id) 拉 BKD /logs，折叠成 Turn
- upsert agent_turns（ON CONFLICT (issue_id, turn_idx) DO UPDATE）

设计原则：
- best-effort：单 issue 失败只 log warning，不抛，不阻断整轮
- 独立 db pool：从 store.db.get_pool() 拿；obs_pool 不参与（主库写）
- 零侵入：agent_turns_collector_enabled=False（默认）时 main.py 不起 task
"""
from __future__ import annotations

import asyncio
import json

import structlog

from .bkd import BKDClient
from .config import settings
from .store import db

log = structlog.get_logger(__name__)

_LOOKBACK_INTERVAL = "24 hours"


async def collect_once() -> dict:
    """单次采集：扫 stage_runs → 拉 BKD logs → upsert agent_turns。"""
    pool = db.get_pool()

    rows = await pool.fetch(
        """
        SELECT sr.req_id,
               sr.bkd_issue_id,
               rs.project_id
          FROM stage_runs sr
          JOIN req_state rs ON rs.req_id = sr.req_id
         WHERE sr.ended_at > NOW() - INTERVAL '24 hours'
           AND sr.ended_at IS NOT NULL
           AND sr.bkd_issue_id IS NOT NULL
         ORDER BY sr.ended_at DESC
        """
    )

    if not rows:
        return {"issues_scanned": 0, "turns_upserted": 0}

    total_upserted = 0
    issues_ok = 0
    issues_failed = 0

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        for row in rows:
            req_id: str = row["req_id"]
            issue_id: str = row["bkd_issue_id"]
            project_id: str = row["project_id"]

            try:
                turns = await bkd.fetch_turns(project_id, issue_id)
                for turn in turns:
                    await pool.execute(
                        """
                        INSERT INTO agent_turns
                            (req_id, issue_id, turn_idx, role, tool_calls,
                             token_in, token_out, token_cache_read,
                             token_cache_create, duration_ms, started_at)
                        VALUES ($1, $2, $3, $4, $5::jsonb,
                                $6, $7, $8, $9, $10, $11)
                        ON CONFLICT (issue_id, turn_idx) DO UPDATE SET
                            role               = EXCLUDED.role,
                            tool_calls         = EXCLUDED.tool_calls,
                            token_in           = EXCLUDED.token_in,
                            token_out          = EXCLUDED.token_out,
                            token_cache_read   = EXCLUDED.token_cache_read,
                            token_cache_create = EXCLUDED.token_cache_create,
                            duration_ms        = EXCLUDED.duration_ms
                        """,
                        req_id,
                        issue_id,
                        turn.turn_idx,
                        turn.role,
                        json.dumps(turn.tool_calls) if turn.tool_calls is not None else None,
                        turn.token_in,
                        turn.token_out,
                        turn.token_cache_read,
                        turn.token_cache_create,
                        turn.duration_ms,
                        turn.started_at,
                    )
                    total_upserted += 1
                issues_ok += 1
                log.debug(
                    "agent_turns_collector.issue_done",
                    issue_id=issue_id,
                    turns=len(turns),
                )
            except Exception as e:
                issues_failed += 1
                log.warning(
                    "agent_turns_collector.issue_failed",
                    issue_id=issue_id,
                    req_id=req_id,
                    error=str(e),
                )

    return {
        "issues_scanned": len(rows),
        "issues_ok": issues_ok,
        "issues_failed": issues_failed,
        "turns_upserted": total_upserted,
    }


async def run_loop() -> None:
    """orchestrator 启动起的后台任务，周期性采集 BKD turn 数据。"""
    interval = settings.agent_turns_collector_interval_sec
    log.info("agent_turns_collector.loop.started", interval_sec=interval)
    while True:
        try:
            result = await collect_once()
            log.debug("agent_turns_collector.tick", result=result)
        except asyncio.CancelledError:
            log.info("agent_turns_collector.loop.stopped")
            raise
        except Exception as e:
            log.exception("agent_turns_collector.loop.error", error=str(e))
        await asyncio.sleep(interval)
