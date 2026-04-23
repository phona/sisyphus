"""state machine 推进器：抽离 webhook 里 decide+cas+dispatch 的循环。

action handler 可以返回 {"emit": "<event-name>"} 触发链式推进。

action handler 抛异常一律走 SESSION_FAILED → ESCALATED（M14c 砍掉 M9 的
fail_kind/idempotent 自动重试，verifier 接管 fail 决策）。
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
from .store import req_state, stage_runs

log = structlog.get_logger(__name__)


# 进 terminal state 时立即清 runner（fire-and-forget；runner_gc 仍周期兜底）
# escalated 保 PVC 给人工 debug，过期由 runner_gc 按 pvc_retain_on_escalate_days 清
_TERMINAL_STATES = {ReqState.DONE, ReqState.ESCALATED}


# M14e/M15：state → stage 名（用于 stage_runs 表）。
_STATE_TO_STAGE: dict[ReqState, str] = {
    ReqState.ANALYZING:              "analyze",
    ReqState.SPEC_LINT_RUNNING:      "spec_lint",
    ReqState.DEV_CROSS_CHECK_RUNNING: "dev_cross_check",
    ReqState.STAGING_TEST_RUNNING:   "staging_test",
    ReqState.PR_CI_RUNNING:          "pr_ci",
    ReqState.ACCEPT_RUNNING:         "accept",
    ReqState.ACCEPT_TEARING_DOWN:    "accept_teardown",
    ReqState.REVIEW_RUNNING:         "verifier",
    ReqState.FIXER_RUNNING:          "fixer",
    ReqState.ARCHIVING:              "archive",
}

# event → stage_runs.outcome 标签。escalate / session.failed 全归 fail。
_EVENT_TO_OUTCOME: dict[Event, str] = {
    Event.ANALYZE_DONE:         "pass",
    Event.SPEC_LINT_PASS:       "pass",
    Event.SPEC_LINT_FAIL:       "fail",
    Event.DEV_CROSS_CHECK_PASS: "pass",
    Event.DEV_CROSS_CHECK_FAIL: "fail",
    Event.STAGING_TEST_PASS:    "pass",
    Event.STAGING_TEST_FAIL:    "fail",
    Event.PR_CI_PASS:           "pass",
    Event.PR_CI_FAIL:           "fail",
    Event.PR_CI_TIMEOUT:        "fail",
    Event.ACCEPT_PASS:          "pass",
    Event.ACCEPT_FAIL:          "fail",
    Event.ACCEPT_ENV_UP_FAIL:   "fail",
    Event.TEARDOWN_DONE_PASS:   "pass",
    Event.TEARDOWN_DONE_FAIL:   "fail",
    Event.ARCHIVE_DONE:         "pass",
    Event.SESSION_FAILED:       "fail",
    Event.VERIFY_PASS:          "pass",
    Event.VERIFY_FIX_NEEDED:    "fix",
    Event.VERIFY_RETRY_CHECKER: "retry",
    Event.VERIFY_ESCALATE:      "escalate",
    Event.FIXER_DONE:           "pass",
}


async def _record_stage_transitions(
    pool: asyncpg.Pool,
    *,
    req_id: str,
    cur_state: ReqState,
    next_state: ReqState,
    event: Event,
) -> None:
    """M14e/M15：CAS 成功后落 stage_runs。

    - 离开 *_RUNNING（cur ≠ next）→ close 上条 run（按事件映射 outcome）
    - 进入 *_RUNNING（cur ≠ next）→ open 新一条 run
    - 自循环（mark_*_reviewed_and_check / apply_verify_pass 等）不动
    任何错误只 log 不抛，避免拖垮主流程。
    """
    if cur_state == next_state:
        return
    try:
        cur_stage = _STATE_TO_STAGE.get(cur_state)
        if cur_stage:
            outcome = _EVENT_TO_OUTCOME.get(event, "cancelled")
            await stage_runs.close_latest_stage_run(
                pool, req_id, cur_stage,
                outcome=outcome,
                fail_reason=event.value if outcome != "pass" else None,
            )
        next_stage = _STATE_TO_STAGE.get(next_state)
        if next_stage:
            await stage_runs.insert_stage_run(
                pool, req_id, next_stage,
                agent_type=next_stage,
            )
    except Exception as e:
        log.warning("engine.stage_runs.write_failed",
                    req_id=req_id, cur=cur_state.value, nxt=next_state.value,
                    error=str(e))

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

    # M14e：落 stage_runs（best-effort）
    await _record_stage_transitions(
        pool, req_id=req_id,
        cur_state=cur_state, next_state=transition.next_state, event=event,
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

    ok, result = await _dispatch_with_retry(
        pool,
        body=body,
        req_id=req_id,
        project_id=project_id,
        tags=tags,
        action_name=transition.action,
        handler=handler,
        ctx=ctx,
        next_state=transition.next_state,
        depth=depth,
    )
    # action terminal fail → _dispatch_with_retry 内部已 emit SESSION_FAILED 走 escalate；
    # 上层直接把 escalate 的 chained 结果原样返回，不再做后续 handler result 的 emit chain
    if not ok:
        return result

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


async def _dispatch_with_retry(
    pool: asyncpg.Pool,
    *,
    body,
    req_id: str,
    project_id: str,
    tags: list[str],
    action_name: str,
    handler,
    ctx: dict,
    next_state: ReqState,
    depth: int,
) -> tuple[bool, dict[str, Any]]:
    """跑 handler；异常一律链式 emit SESSION_FAILED 进 escalate（M14c：verifier 接管 fail 决策）。

    返回 (ok, payload)：
        ok=True, payload = handler 的 result dict（上层继续做 emit chain）
        ok=False, payload = escalate 汇总字典（error / escalated / chained 等），
                            上层应直接返回不再处理
    """
    issue_id = getattr(body, "issueId", None)
    started = time.monotonic()
    try:
        result = await handler(body=body, req_id=req_id, tags=tags, ctx=ctx)
    except Exception as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        log.exception("engine.action_failed", action=action_name, error=str(e))
        await obs.record_event(
            "action.failed",
            req_id=req_id, issue_id=issue_id, tags=tags,
            router_action=action_name,
            duration_ms=duration_ms, error_msg=str(e)[:500],
        )
        chained = await _emit_escalate(
            pool, body=body, req_id=req_id, project_id=project_id,
            tags=tags, fallback_state=next_state, depth=depth,
            error_reason=str(e)[:200],
        )
        return False, {
            "action": "error",
            "reason": str(e),
            "escalated": True,
            "chained": chained,
        }

    await obs.record_event(
        "action.executed",
        req_id=req_id, issue_id=issue_id, tags=tags,
        router_action=action_name,
        duration_ms=int((time.monotonic() - started) * 1000),
        extras=result if isinstance(result, dict) else None,
    )
    return True, (result if isinstance(result, dict) else {})


async def _emit_escalate(
    pool: asyncpg.Pool,
    *,
    body,
    req_id: str,
    project_id: str,
    tags: list[str],
    fallback_state: ReqState,
    depth: int,
    error_reason: str,
) -> dict[str, Any]:
    """action handler 彻底失败 → 从当前状态 emit SESSION_FAILED 进 escalate 链。

    读最新 state（cas 已推进到 next_state；handler 部分执行可能有 ctx 改动）。
    SESSION_FAILED 对所有 *_RUNNING 态都有 transition → ESCALATED + escalate action。
    """
    new_row = await req_state.get(pool, req_id)
    cur_state = new_row.state if new_row else fallback_state
    cur_ctx = new_row.context if new_row else {}
    # 把失败原因暴露给 escalate action（ctx + body.event）— escalate 读 body.event 打 tag
    # 这里不改 body.event（signature 限制）；仅保留 ctx 里的诊断信息
    await req_state.update_context(pool, req_id, {
        "action_escalate_reason": error_reason[:200],
    })
    return await step(
        pool,
        body=body,
        req_id=req_id,
        project_id=project_id,
        tags=tags,
        cur_state=cur_state,
        ctx=cur_ctx,
        event=Event.SESSION_FAILED,
        depth=depth + 1,
    )
