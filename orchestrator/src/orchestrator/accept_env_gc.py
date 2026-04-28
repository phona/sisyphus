"""Accept env GC 后台任务（REQ-accept-env-gc-1777377950）。

每 N 秒扫一次：
- 列 cluster 中 `accept-{req_id.lower()}` namespace
- 对应 REQ 已是终态（done / escalated）或 req_state 里找不到（orphan）→ 删 namespace
- 非终态 REQ 的 namespace 保留

设计要点：
- 纯 infra，不经 BKD agent（直接 sisyphus 调 K8s API）
- 幂等：namespace 删了再删 404 = no-op
- 失败只 warning，不阻塞主进程
- 跟 runner_gc 同模式：gc_once + run_loop + _last_result
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from kubernetes.client import ApiException

from . import k8s_runner
from .config import settings
from .store import db

log = structlog.get_logger(__name__)

_TERMINAL_STATES = {"done", "escalated"}

# 上次 gc_once() 的结果（timer loop 和 admin trigger 共用同一槽）。
_last_gc_result: dict | None = None


def get_last_result() -> dict | None:
    """返回上次 gc_once() 的结果，包含 ran_at；首次 GC 前为 None。"""
    return _last_gc_result


def _parse_req_id_from_namespace(ns_name: str) -> str | None:
    """从 `accept-req-xxx` 解析 req_id。非 accept- 前缀返回 None。"""
    prefix = "accept-"
    if not ns_name.startswith(prefix):
        return None
    suffix = ns_name[len(prefix):]
    if suffix.lower().startswith("req-"):
        return suffix.upper()
    return suffix


async def _active_req_ids() -> set[str]:
    """保留集 = 非终态 REQ（accept env 还在用）。"""
    pool = db.get_pool()
    rows = await pool.fetch("SELECT req_id, state FROM req_state")
    return {r["req_id"] for r in rows if r["state"] not in _TERMINAL_STATES}


async def gc_once() -> dict:
    """单次 GC pass：扫描 accept-* namespace，删 terminal / orphan 的。

    每次调用（包括 skipped）都更新模块级 _last_gc_result（附 ran_at）。
    """
    global _last_gc_result

    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        result: dict = {
            "skipped": "no runner controller",
            "ran_at": datetime.now(UTC).isoformat(),
        }
        _last_gc_result = result
        return result

    active = await _active_req_ids()
    ns_list = await rc.list_accept_env_namespaces()

    cleaned: list[str] = []
    kept: list[str] = []
    for ns_name in ns_list:
        req_id = _parse_req_id_from_namespace(ns_name)
        if req_id is None:
            # 不是 accept- 前缀的 namespace（理论上 list_accept_env_namespaces 不会返）
            continue
        if req_id in active:
            kept.append(ns_name)
            continue
        try:
            await rc.delete_namespace(ns_name)
            cleaned.append(ns_name)
        except ApiException as e:
            if e.status == 404:
                # 已被别处删了，算清理成功
                cleaned.append(ns_name)
            else:
                log.warning("accept_env_gc.delete_failed", namespace=ns_name, error=str(e))
        except Exception as e:
            log.warning("accept_env_gc.delete_failed", namespace=ns_name, error=str(e))

    result = {
        "cleaned_namespaces": cleaned,
        "kept_namespaces": kept,
        "cleaned_count": len(cleaned),
        "kept_count": len(kept),
        "ran_at": datetime.now(UTC).isoformat(),
    }
    _last_gc_result = result
    return result


async def run_loop() -> None:
    """orchestrator 启动起的后台任务，周期性扫 + GC。"""
    interval = settings.accept_env_gc_interval_sec
    log.info("accept_env_gc.loop.started", interval_sec=interval)
    while True:
        try:
            result = await gc_once()
            if result.get("cleaned_namespaces"):
                log.warning(
                    "accept_env_gc.swept",
                    count=len(result["cleaned_namespaces"]),
                    namespaces=result["cleaned_namespaces"],
                )
            else:
                log.debug("accept_env_gc.tick", result=result)
        except asyncio.CancelledError:
            log.info("accept_env_gc.loop.stopped")
            raise
        except Exception as e:
            log.exception("accept_env_gc.loop.error", error=str(e))
        await asyncio.sleep(interval)
