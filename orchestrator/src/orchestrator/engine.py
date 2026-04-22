"""state machine 推进器：抽离 webhook 里 decide+cas+dispatch 的循环。

action handler 可以返回 {"emit": "<event-name>"} 触发链式推进
（例如 mark_spec_reviewed_and_check 检测到 N/N 后 emit spec.all-passed → create_dev）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import asyncpg
import structlog

from . import k8s_runner
from . import observability as obs
from .actions import REGISTRY
from .state import Event, ReqState, decide
from .store import req_state

log = structlog.get_logger(__name__)


# 进 terminal state 时立即清 runner（fire-and-forget；runner_gc 仍周期兜底）
# escalated 保 PVC 给人工 debug，过期由 runner_gc 按 pvc_retain_on_escalate_days 清
_TERMINAL_STATES = {ReqState.DONE, ReqState.ESCALATED}

# 持引用防 fire-and-forget task 被 GC（done_callback 自清）
_cleanup_tasks: set[asyncio.Task] = set()


async def _cleanup_runner_on_terminal(req_id: str, terminal_state: ReqState) -> None:
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.debug("runner.cleanup_no_controller", req_id=req_id, error=str(e))
        return
    try:
        await rc.cleanup_runner(
            req_id,
            retain_pvc=(terminal_state == ReqState.ESCALATED),
        )
        log.info("runner.cleanup_on_terminal",
                 req_id=req_id, terminal_state=terminal_state.value)
    except Exception as e:
        # cleanup 失败别回压状态机；runner_gc 兜底
        log.warning("runner.cleanup_failed", req_id=req_id, error=str(e))


async def step(
    pool: asyncpg.Pool,
    *,
    body,
    req_id: str,
    project_id: str,
    tags: list[str],
    cur_state: ReqState,
    ctx: dict,
    event: Event,
    depth: int = 0,
) -> dict[str, Any]:
    """run one (cur_state, event) transition + dispatch action. Recurse on emit.

    返回最后一次 dispatch 的结果 dict。
    """
    # 12 足够：test_mode 全跳要 7 emit 才到 done；正常流程 emit 一般 ≤2。
    # 用来防 emit 死循环，不是常规流量限制。
    if depth > 12:
        log.error("engine.recursion_too_deep", req_id=req_id, evt=event.value)
        return {"action": "error", "reason": "engine recursion >12"}

    transition = decide(cur_state, event)
    if transition is None:
        log.info("engine.illegal_transition", req_id=req_id, state=cur_state.value, evt=event.value)
        return {"action": "skip", "reason": f"no transition {cur_state.value}+{event.value}"}

    advanced = await req_state.cas_transition(
        pool, req_id, cur_state, transition.next_state, event, transition.action,
    )
    if not advanced:
        log.info("engine.cas_failed", req_id=req_id, expected=cur_state.value)
        return {"action": "skip", "reason": "concurrent state change"}

    log.info(
        "engine.transitioned",
        req_id=req_id,
        from_state=cur_state.value,
        to_state=transition.next_state.value,
        evt=event.value,
        action=transition.action,
    )

    # M10：转 terminal state 时立即清 runner（fire-and-forget）
    if transition.next_state in _TERMINAL_STATES:
        task = asyncio.create_task(
            _cleanup_runner_on_terminal(req_id, transition.next_state)
        )
        _cleanup_tasks.add(task)
        task.add_done_callback(_cleanup_tasks.discard)

    await obs.record_event(
        "router.decision",
        req_id=req_id, issue_id=getattr(body, "issueId", None), tags=tags,
        router_action=transition.action, router_reason=transition.reason,
        extras={
            "from_state": cur_state.value,
            "to_state": transition.next_state.value,
            "event": event.value,
        },
    )

    if transition.action is None:
        return {"action": "no-op", "next_state": transition.next_state.value}

    handler = REGISTRY.get(transition.action)
    if handler is None:
        log.error("engine.action_not_registered", action=transition.action)
        return {"action": "error", "reason": f"action {transition.action} not registered"}

    started = time.monotonic()
    try:
        result = await handler(body=body, req_id=req_id, tags=tags, ctx=ctx)
    except Exception as e:
        log.exception("engine.action_failed", action=transition.action, error=str(e))
        await obs.record_event(
            "action.failed",
            req_id=req_id, issue_id=getattr(body, "issueId", None), tags=tags,
            router_action=transition.action,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_msg=str(e)[:500],
        )
        return {"action": "error", "reason": str(e)}

    await obs.record_event(
        "action.executed",
        req_id=req_id, issue_id=getattr(body, "issueId", None), tags=tags,
        router_action=transition.action,
        duration_ms=int((time.monotonic() - started) * 1000),
        extras=result if isinstance(result, dict) else None,
    )

    base_result = {
        "action": transition.action,
        "next_state": transition.next_state.value,
        "result": result,
    }

    # 链式 emit：handler 主动让状态机继续走
    emit_name = result.get("emit") if isinstance(result, dict) else None
    if emit_name:
        try:
            next_event = Event(emit_name)
        except ValueError:
            log.error("engine.invalid_emit", emit=emit_name)
            return base_result
        # reload state + ctx（可能被 handler 更新过）
        new_row = await req_state.get(pool, req_id)
        if new_row is None:
            return base_result
        chain = await step(
            pool,
            body=body,
            req_id=req_id,
            project_id=project_id,
            tags=tags,
            cur_state=new_row.state,
            ctx=new_row.context,
            event=next_event,
            depth=depth + 1,
        )
        base_result["chained"] = chain

    return base_result
