"""Runner GC 后台任务（v0.2-S5）。

每 N 秒扫一次：
- Pod GC：terminal REQ 的 Pod 立即清（escalated 不绑 retention，释放内存）
- PVC GC：done 立即销 PVC；escalated PVC 保留 retention 给人 debug；磁盘压力时全清
- 完全找不到 req_state 的 runner（孤儿：orchestrator 丢数据 / 手动清了 PG）：也销

不会销正在跑的 REQ 资源（in-flight state）。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from kubernetes.client import ApiException

from . import k8s_runner
from .config import settings
from .store import db

log = structlog.get_logger(__name__)


# 状态机里"终态"和"进行中"的区分
_TERMINAL_STATES = {"done", "escalated"}

# 进程级 flag：node_disk_usage_ratio 拿到 403（RBAC 缺 nodes:list）时置 True，
# 后续 GC tick 直接 short-circuit 跳过 disk-check，不再发请求/打日志。
# 重启 orchestrator 会重新探测一次。
_DISK_CHECK_DISABLED = False


async def _pod_keep_req_ids() -> set[str]:
    """Pod 保留集 = 仅 non-terminal REQ。

    done / escalated 都是 terminal —— Pod 立即可清。escalated Pod 占 512Mi
    内存 request、8Gi limit，retention 是 PVC 给人 debug 用的，不该绑 Pod
    生命周期。zombie Pod 整段 retention 活着会挤掉别 REQ 的调度容量。
    """
    pool = db.get_pool()
    rows = await pool.fetch("SELECT req_id, state FROM req_state")
    return {r["req_id"] for r in rows if r["state"] not in _TERMINAL_STATES}


async def _pvc_keep_req_ids(*, ignore_retention: bool = False) -> set[str]:
    """PVC 保留集 = non-terminal + escalated 在 retention 内（除非磁盘压力）。

    done 立即销 PVC（无 debug 价值，磁盘释放优先）。
    ignore_retention=True：磁盘压力下，escalated 也不留 retention，全清。
    """
    pool = db.get_pool()
    rows = await pool.fetch(
        "SELECT req_id, state, updated_at, context FROM req_state",
    )
    keep: set[str] = set()
    now = datetime.now(UTC)
    retention = timedelta(days=settings.pvc_retain_on_escalate_days)
    for r in rows:
        state = r["state"]
        if state not in _TERMINAL_STATES:
            keep.add(r["req_id"])
            continue
        if state == "done":
            continue
        if state == "escalated" and not ignore_retention:
            updated_at = r["updated_at"]
            if updated_at and (now - updated_at) < retention:
                keep.add(r["req_id"])
    return keep


async def gc_once() -> dict:
    """单次 GC pass。Pod 和 PVC 各扫一次（保留集不同）。

    Pod GC：terminal REQ 的 Pod 立即清（escalated 不再绑 retention，释放内存）。
    PVC GC：escalated REQ 的 PVC 留 retention 给人 debug；磁盘压力时全清。

    磁盘压力 (> threshold) 仅影响 PVC keep set —— Pod keep set 永远不含
    terminal state，跟磁盘无关。
    """
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        # dev 环境没 K8s，跳过
        return {"skipped": "no runner controller"}

    # 检查磁盘压力。压时 escalated PVC 也立即清（不留 retention）。
    # 若上一次因 RBAC 403 已禁用 disk-check，直接跳，不再发请求。
    global _DISK_CHECK_DISABLED
    disk_pressure = False
    if not _DISK_CHECK_DISABLED:
        try:
            ratio = await rc.node_disk_usage_ratio()
            if ratio > settings.runner_gc_disk_pressure_threshold:
                log.warning("runner_gc.disk_pressure", ratio=round(ratio, 2),
                            threshold=settings.runner_gc_disk_pressure_threshold)
                disk_pressure = True
        except ApiException as e:
            if e.status == 403:
                # RBAC 没给 nodes:list（orchestrator Role 是 ns-scoped），
                # 永久降级：之后不再探测，留 retention-only 路径。
                # INFO 不是 WARNING：namespace-scoped Role 是预期部署形态，
                # 这条 log 不应每次 pod 重启就污染 warning 流。
                _DISK_CHECK_DISABLED = True
                log.info("runner_gc.disk_check_rbac_denied",
                         hint="ServiceAccount lacks cluster-scoped nodes:list; "
                              "disk-pressure emergency purge disabled until restart")
            else:
                log.debug("runner_gc.disk_check_failed", error=str(e), status=e.status)
        except Exception as e:
            # 取不到磁盘指标 → 退回正常 retention 模式
            log.debug("runner_gc.disk_check_failed", error=str(e))

    pod_keep = await _pod_keep_req_ids()
    pvc_keep = await _pvc_keep_req_ids(ignore_retention=disk_pressure)
    cleaned_pods = await rc.gc_orphan_pods(pod_keep)
    cleaned_pvcs = await rc.gc_orphan_pvcs(pvc_keep)
    return {
        "cleaned_pods": cleaned_pods,
        "cleaned_pvcs": cleaned_pvcs,
        "pod_kept": len(pod_keep),
        "pvc_kept": len(pvc_keep),
        "disk_pressure": disk_pressure,
    }


async def run_loop() -> None:
    """orchestrator 启动起的后台任务，周期性扫 + GC。"""
    interval = settings.runner_gc_interval_sec
    log.info("runner_gc.loop.started", interval_sec=interval)
    while True:
        try:
            result = await gc_once()
            if result.get("cleaned_pods") or result.get("cleaned_pvcs"):
                log.warning(
                    "runner_gc.swept",
                    pods=result.get("cleaned_pods", []),
                    pvcs=result.get("cleaned_pvcs", []),
                )
            else:
                log.debug("runner_gc.tick", result=result)
        except asyncio.CancelledError:
            log.info("runner_gc.loop.stopped")
            raise
        except Exception as e:
            log.exception("runner_gc.loop.error", error=str(e))
        await asyncio.sleep(interval)
