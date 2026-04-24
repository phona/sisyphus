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


async def _active_req_ids(*, ignore_retention: bool = False) -> set[str]:
    """列出所有 non-terminal REQ 的 req_id；或 terminal 但仍在保留期内的也算 active。

    ignore_retention=True：磁盘压力下，escalated 也不留 retention，全清。
    """
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
        # escalated：看是否还在保留期（磁盘压力时跳过 retention 直接清）
        if state == "escalated" and not ignore_retention:
            updated_at = r["updated_at"]
            if updated_at and (now - updated_at) < retention:
                active.add(r["req_id"])
    return active


async def gc_once() -> dict:
    """单次 GC pass。返回 {cleaned: [...]}。

    磁盘压力 (> threshold) 时：忽略 escalated retention，全清 non-active PVC（紧急疏散）。
    """
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        # dev 环境没 K8s，跳过
        return {"skipped": "no runner controller"}

    # 检查磁盘压力。压时 escalated PVC 也立即清（不留 retention）。
    disk_pressure = False
    try:
        ratio = await rc.node_disk_usage_ratio()
        if ratio > settings.runner_gc_disk_pressure_threshold:
            log.warning("runner_gc.disk_pressure", ratio=round(ratio, 2),
                        threshold=settings.runner_gc_disk_pressure_threshold)
            disk_pressure = True
    except Exception as e:
        # 取不到磁盘指标 → 退回正常 retention 模式
        log.debug("runner_gc.disk_check_failed", error=str(e))

    active = await _active_req_ids(ignore_retention=disk_pressure)
    cleaned = await rc.gc_orphans(active)
    return {
        "cleaned": cleaned,
        "active_kept": len(active),
        "disk_pressure": disk_pressure,
    }


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
