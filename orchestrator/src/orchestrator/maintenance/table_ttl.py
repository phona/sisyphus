"""Table TTL 后台清理任务。

每 ttl_cleanup_interval_sec 秒跑一次，删 4 张增长表的过期行：
- event_seen        : seen_at  > ttl_event_seen_days 天
- dispatch_slugs    : created_at > ttl_dispatch_slugs_days 天
- verifier_decisions: made_at > ttl_verifier_decisions_days 天
- stage_runs (closed, ended_at IS NOT NULL): ended_at > ttl_stage_runs_closed_days 天
  ended_at IS NULL 的行永远不删（排错保留）
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from ..config import settings
from ..store import db

log = structlog.get_logger(__name__)


async def run_ttl_cleanup(pool: Any, cfg: Any | None = None) -> dict:
    """单次 TTL 清理。返回各表 {before, after, deleted} 统计。

    cfg 默认读 settings（模块全局）；测试可传入自定义对象覆盖 TTL 天数。
    """
    if cfg is None:
        cfg = settings

    now = datetime.now(UTC)

    # ── event_seen ──────────────────────────────────────────────────────
    cutoff_es = now - timedelta(days=cfg.ttl_event_seen_days)
    before_es: int = await pool.fetchval("SELECT COUNT(*) FROM event_seen")
    await pool.execute("DELETE FROM event_seen WHERE seen_at < $1", cutoff_es)
    after_es: int = await pool.fetchval("SELECT COUNT(*) FROM event_seen")

    # ── dispatch_slugs ──────────────────────────────────────────────────
    cutoff_ds = now - timedelta(days=cfg.ttl_dispatch_slugs_days)
    before_ds: int = await pool.fetchval("SELECT COUNT(*) FROM dispatch_slugs")
    await pool.execute("DELETE FROM dispatch_slugs WHERE created_at < $1", cutoff_ds)
    after_ds: int = await pool.fetchval("SELECT COUNT(*) FROM dispatch_slugs")

    # ── verifier_decisions ──────────────────────────────────────────────
    cutoff_vd = now - timedelta(days=cfg.ttl_verifier_decisions_days)
    before_vd: int = await pool.fetchval("SELECT COUNT(*) FROM verifier_decisions")
    await pool.execute("DELETE FROM verifier_decisions WHERE made_at < $1", cutoff_vd)
    after_vd: int = await pool.fetchval("SELECT COUNT(*) FROM verifier_decisions")

    # ── stage_runs (closed only) ─────────────────────────────────────────
    # ended_at IS NULL = still running or never closed — never touch those.
    cutoff_sr = now - timedelta(days=cfg.ttl_stage_runs_closed_days)
    before_sr: int = await pool.fetchval(
        "SELECT COUNT(*) FROM stage_runs WHERE ended_at IS NOT NULL"
    )
    await pool.execute(
        "DELETE FROM stage_runs WHERE ended_at IS NOT NULL AND ended_at < $1", cutoff_sr
    )
    after_sr: int = await pool.fetchval(
        "SELECT COUNT(*) FROM stage_runs WHERE ended_at IS NOT NULL"
    )

    summary = {
        "event_seen": {"before": before_es, "after": after_es, "deleted": before_es - after_es},
        "dispatch_slugs": {"before": before_ds, "after": after_ds, "deleted": before_ds - after_ds},
        "verifier_decisions": {"before": before_vd, "after": after_vd, "deleted": before_vd - after_vd},
        "stage_runs": {"before": before_sr, "after": after_sr, "deleted": before_sr - after_sr},
    }
    log.info(
        "table_ttl.cleanup.summary",
        event_seen_before=before_es, event_seen_deleted=before_es - after_es,
        dispatch_slugs_before=before_ds, dispatch_slugs_deleted=before_ds - after_ds,
        verifier_decisions_before=before_vd, verifier_decisions_deleted=before_vd - after_vd,
        stage_runs_before=before_sr, stage_runs_deleted=before_sr - after_sr,
    )
    return summary


async def run_loop() -> None:
    """orchestrator 启动起的后台任务，周期性 TTL 清理。

    ttl_cleanup_enabled=False 时立即返回（loop 退出）。
    """
    if not settings.ttl_cleanup_enabled:
        log.info("table_ttl.disabled")
        return

    interval = settings.ttl_cleanup_interval_sec
    log.info("table_ttl.loop.started", interval_sec=interval)
    while True:
        try:
            pool = db.get_pool()
            await run_ttl_cleanup(pool)
        except asyncio.CancelledError:
            log.info("table_ttl.loop.stopped")
            raise
        except Exception as e:
            log.warning("table_ttl.error", error=str(e))
        await asyncio.sleep(interval)
