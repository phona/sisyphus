"""accept_env_gc：清理孤儿 accept env namespace（v0.1 minimal skeleton）。

create_accept 通过 make accept-env-up 在 K8s 集群创建 namespace accept-{req_id.lower()}
（含 helm release）。teardown_accept_env best-effort 跑 make accept-env-down，失败只
warning 不阻塞状态机。本 GC 补位：周期扫 accept-* namespace，对终态 REQ 直接 delete
namespace（cascade 清所有资源）。

"no helm RBAC"：不调 helm uninstall（需要 ClusterRole），只 delete namespace。
RBAC 403 时进程级禁用（同 runner_gc 磁盘检测），不污染日志。
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

_TERMINAL_STATES = {"done", "escalated"}

# 403 时进程级禁用，避免每轮 GC 都打日志；重启后重新探测一次
_NS_RBAC_DISABLED = False

_ACCEPT_NS_PREFIX = "accept-"


def _req_lower_from_ns(ns_name: str) -> str | None:
    """'accept-req-foo-123' → 'req-foo-123'（DB req_id.lower()）。非 accept-req-* 返 None。"""
    if not ns_name.startswith(_ACCEPT_NS_PREFIX):
        return None
    suffix = ns_name[len(_ACCEPT_NS_PREFIX):]
    if not suffix.startswith("req-"):
        return None
    return suffix


async def _stale_req_ids_lower() -> set[str]:
    """返回需要 GC accept namespace 的 req_id.lower() 集合。

    规则同 runner_gc：done → 立即清；escalated → 超 retention 才清。
    """
    pool = db.get_pool()
    rows = await pool.fetch("SELECT req_id, state, updated_at FROM req_state")
    stale: set[str] = set()
    now = datetime.now(UTC)
    retention = timedelta(days=settings.pvc_retain_on_escalate_days)
    for r in rows:
        state = r["state"]
        if state not in _TERMINAL_STATES:
            continue
        if state == "done":
            stale.add(r["req_id"].lower())
        elif state == "escalated":
            updated_at = r["updated_at"]
            if updated_at and (now - updated_at) >= retention:
                stale.add(r["req_id"].lower())
    return stale


async def gc_once() -> dict:
    """单次 GC：找终态 REQ 的孤儿 accept namespace 并删除。"""
    global _NS_RBAC_DISABLED

    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        return {"skipped": "no runner controller"}

    if _NS_RBAC_DISABLED:
        return {"skipped": "namespace rbac disabled"}

    # list_namespace 是 cluster-scoped 调用；ServiceAccount 缺权限时 403 进程级禁用
    try:
        ns_list = await asyncio.to_thread(rc.core_v1.list_namespace)
    except ApiException as e:
        if e.status == 403:
            _NS_RBAC_DISABLED = True
            log.info(
                "accept_env_gc.rbac_denied",
                hint="ServiceAccount lacks cluster-scoped namespaces:list; "
                     "accept_env_gc disabled until restart",
            )
            return {"skipped": "namespace list rbac denied"}
        log.debug("accept_env_gc.list_ns_failed", error=str(e), status=e.status)
        return {"error": f"list_namespace failed: {e.status}"}

    stale_lower = await _stale_req_ids_lower()
    cleaned: list[str] = []

    for ns in ns_list.items:
        ns_name = ns.metadata.name
        req_lower = _req_lower_from_ns(ns_name)
        if req_lower is None or req_lower not in stale_lower:
            continue

        try:
            await asyncio.to_thread(rc.core_v1.delete_namespace, ns_name)
            log.info("accept_env_gc.ns_deleted", namespace=ns_name, req_lower=req_lower)
            cleaned.append(ns_name)
        except ApiException as e:
            if e.status == 404:
                pass  # 已被删，幂等
            elif e.status == 403:
                _NS_RBAC_DISABLED = True
                log.info(
                    "accept_env_gc.rbac_denied",
                    namespace=ns_name,
                    hint="ServiceAccount lacks namespace delete permission; "
                         "accept_env_gc disabled until restart",
                )
                break
            else:
                log.warning(
                    "accept_env_gc.delete_failed",
                    namespace=ns_name, error=str(e), status=e.status,
                )

    if cleaned:
        log.info("accept_env_gc.swept", count=len(cleaned), namespaces=cleaned)
    return {"cleaned": cleaned, "stale_req_count": len(stale_lower)}


async def run_loop() -> None:
    """orchestrator 启动的后台 GC 任务，周期扫 accept namespace。"""
    interval = settings.accept_env_gc_interval_sec
    log.info("accept_env_gc.loop.started", interval_sec=interval)
    while True:
        try:
            result = await gc_once()
            if result.get("cleaned"):
                log.info("accept_env_gc.tick.swept", result=result)
            else:
                log.debug("accept_env_gc.tick", result=result)
        except asyncio.CancelledError:
            log.info("accept_env_gc.loop.stopped")
            raise
        except Exception as e:
            log.exception("accept_env_gc.loop.error", error=str(e))
        await asyncio.sleep(interval)
