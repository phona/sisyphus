"""accept_env_gc — 清理 accept-req-* namespace 下的孤儿资源。

accept 环境（helm install 到 `accept-{req_id}` namespace）由 runner pod 内
`make accept-env-up` 创建，`make accept-env-down` / teardown_accept_env 负责拆。
当 teardown 失败或漏跑时，本模块周期性扫描并级联删除无对应 running REQ 的 namespace。

设计原则：
- 只删 namespace（级联清理 Pod/Service/ConfigMap/Deployment 等全部资源）
- 保留 set = DB 中 non-terminal 状态的 req_id（跟 runner_gc 一致）
- 404 视为 no-op，不阻塞
- 失败只 log，不抛（runner_gc 同模式）
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from . import k8s_runner
from .config import settings
from .store import db

log = structlog.get_logger(__name__)

_TERMINAL_STATES = {"done", "escalated"}

# 上次 gc_once() 的结果（timer loop 和 admin trigger 共用同一槽）。
# orchestrator 重启后清零。
_last_gc_result: dict | None = None


def get_last_result() -> dict | None:
    """返回上次 gc_once() 的结果，包含 ran_at；首次 GC 前为 None。"""
    return _last_gc_result


async def _running_req_ids() -> set[str]:
    """返回 DB 中所有 non-terminal 状态的 req_id。"""
    pool = db.get_pool()
    rows = await pool.fetch("SELECT req_id, state FROM req_state")
    return {r["req_id"] for r in rows if r["state"] not in _TERMINAL_STATES}


async def gc_once() -> dict:
    """单次 GC pass：扫描 accept-req-* namespace，删孤儿。

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

    keep = await _running_req_ids()
    cleaned = await rc.gc_accept_env_namespaces(keep)
    result = {
        "cleaned_namespaces": cleaned,
        "keep_count": len(keep),
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
