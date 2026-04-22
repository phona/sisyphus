"""Runner GC 后台任务（v0.2-S5）。

每 N 秒扫一次：
- state=done 的 REQ：其 Pod + PVC 立即销（释放磁盘）
- state=escalated 且 escalated_at > retention 天的：也销
- 完全找不到 req_state 的 runner（孤儿：orchestrator 丢数据 / 手动清了 PG）：也销

不会销正在跑的 REQ 资源（in-flight state）。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from . import k8s_runner
from .config import settings
from .store import db

log = structlog.get_logger(__name__)


# 状态机里"终态"和"进行中"的区分
_TERMINAL_STATES = {"done", "escalated"}


async def _active_req_ids() -> set[str]:
    """列出所有 non-terminal REQ 的 req_id（大写）；
    或 terminal 但还在保留期内的也算 active（先不清）。"""
    pool = db.get_pool()
    rows = await pool.fetch(
        "SELECT req_id, state, updated_at, context FROM req_state",
    )
    active: set[str] = set()
    now = datetime.now(UTC)
    retention = timedelta(days=settings.pvc_retain_on_escalate_days)
    for r in rows:
        state = r["state"]
        if state not in _TERMINAL_STATES:
            active.add(r["req_id"])
            continue
        if state == "done":
            continue   # done 的 runner 立即销
        # escalated：看是否还在保留期
        if state == "escalated":
            updated_at = r["updated_at"]
            if updated_at and (now - updated_at) < retention:
                active.add(r["req_id"])
    return active


async def gc_once() -> dict:
    """单次 GC pass。返回 {cleaned: [...]}。"""
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        # dev 环境没 K8s，跳过
        return {"skipped": "no runner controller"}

    active = await _active_req_ids()
    cleaned = await rc.gc_orphans(active)
    return {"cleaned": cleaned, "active_kept": len(active)}


async def run_loop() -> None:
    """orchestrator 启动起的后台任务，周期性扫 + GC。"""
    interval = settings.runner_gc_interval_sec
    log.info("runner_gc.loop.started", interval_sec=interval)
    while True:
        try:
            result = await gc_once()
            if result.get("cleaned"):
                log.warning("runner_gc.swept", cleaned=result["cleaned"])
            else:
                log.debug("runner_gc.tick", result=result)
        except asyncio.CancelledError:
            log.info("runner_gc.loop.stopped")
            raise
        except Exception as e:
            log.exception("runner_gc.loop.error", error=str(e))
        await asyncio.sleep(interval)
