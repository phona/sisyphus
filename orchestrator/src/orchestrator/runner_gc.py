"""Runner GC 后台任务（v0.2-S5）。

每 N 秒扫一次：
- state=done 的 REQ：其 Pod + PVC 立即销（释放磁盘）
- state=escalated 且 escalated_at > retention 天的：也销
- 完全找不到 req_state 的 runner（孤儿：orchestrator 丢数据 / 手动清了 PG）：也销

PVC GC 补丁（gc_pvcs）：
- terminal=done 立即删 PVC
- terminal=escalated 保留 24h
- disk pressure > 80% 时强清所有非 active REQ 的 PVC

不会销正在跑的 REQ 资源（in-flight state）。
"""
from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta

import structlog

from . import k8s_runner
from .config import settings
from .store import db

log = structlog.get_logger(__name__)


# 状态机里"终态"和"进行中"的区分
_TERMINAL_STATES = {"done", "escalated"}

# escalated PVC 保留 24h 给人翻 workspace（独立于 pvc_retain_on_escalate_days 的 Pod GC 保留期）
_PVC_ESCALATED_RETAIN_H = 24


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


async def _disk_pressure() -> float:
    """返回根文件系统磁盘使用率（0.0–1.0）。"""
    usage = await asyncio.to_thread(shutil.disk_usage, "/")
    return usage.used / usage.total


async def gc_pvcs() -> dict:
    """PVC 专项 GC：

    - done REQ → 立即删 PVC
    - escalated REQ → 超 24h 删 PVC
    - disk pressure > 80% → 强清所有非 active REQ 的 PVC
    """
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        return {"skipped": "no runner controller"}

    pool = db.get_pool()
    rows = await pool.fetch(
        "SELECT req_id, state, updated_at FROM req_state "
        "WHERE state IN ('done', 'escalated')",
    )

    pressure = await _disk_pressure()
    now = datetime.now(UTC)
    deleted: list[str] = []

    # 先算 active req ids（用于 disk pressure 强清）
    active_rows = await pool.fetch("SELECT req_id FROM req_state WHERE state <> ALL($1::text[])",
                                   list(_TERMINAL_STATES))
    active_req_ids = {r["req_id"].lower() for r in active_rows}

    for r in rows:
        req_id = r["req_id"]
        state = r["state"]
        updated_at = r["updated_at"]

        should_delete = False
        if state == "done":
            should_delete = True
        elif state == "escalated" and updated_at:
            age_h = (now - updated_at).total_seconds() / 3600
            if age_h > _PVC_ESCALATED_RETAIN_H:
                should_delete = True

        # disk pressure 强清：所有非 active 的 PVC
        if not should_delete and pressure > 0.8:
            if req_id.lower() not in active_req_ids:
                should_delete = True

        if should_delete:
            try:
                ok = await rc.delete_pvc(req_id)
                if ok:
                    deleted.append(req_id)
            except Exception as e:
                log.warning("runner_gc.pvc_delete_failed", req_id=req_id, error=str(e))

    if pressure > 0.8:
        log.warning("runner_gc.disk_pressure_purge",
                    usage_pct=f"{pressure:.0%}", deleted=deleted)

    return {"pvc_deleted": deleted, "disk_pressure": round(pressure, 3)}


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

            pvc_result = await gc_pvcs()
            if pvc_result.get("pvc_deleted"):
                log.warning("runner_gc.pvcs_swept", **pvc_result)
        except asyncio.CancelledError:
            log.info("runner_gc.loop.stopped")
            raise
        except Exception as e:
            log.exception("runner_gc.loop.error", error=str(e))
        await asyncio.sleep(interval)
