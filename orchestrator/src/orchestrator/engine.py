"""state machine 推进器：抽离 webhook 里 decide+cas+dispatch 的循环。

action handler 可以返回 {"emit": "<event-name>"} 触发链式推进。

action handler 抛异常一律走 SESSION_FAILED → ESCALATED（M14c 砍掉 M9 的
fail_kind/idempotent 自动重试，verifier 接管 fail 决策）。
"""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

import asyncpg
import structlog

from . import k8s_runner
from . import observability as obs
from .actions import REGISTRY
from .bkd import BKDClient
from .config import settings
from .state import Event, ReqState, decide
from .store import req_state, stage_runs

log = structlog.get_logger(__name__)


# 进 terminal state 时立即清 runner（fire-and-forget；runner_gc 仍周期兜底）
# escalated 保 PVC 给人工 debug，过期由 runner_gc 按 pvc_retain_on_escalate_days 清
_TERMINAL_STATES = {ReqState.DONE, ReqState.ESCALATED}

# REQ-bkd-hitl-end-to-end-loop-1777273753: 把 sisyphus 终态镜像到 BKD intent issue
# 的 statusId，让看板"完成 / 待审查"列跟 req_state.state 保持同步。
#   DONE     → "done"   （BKD 看板"完成"列）
#   ESCALATED → "review" （BKD 看板"待审查"列；跟 webhook._push_upstream_status
#               对 verifier-decision-escalate 的处理对齐 —— 让人能在 BKD UI
#               定位到该 follow-up 哪条 issue）
_TERMINAL_STATE_TO_BKD_STATUS_ID: dict[ReqState, str] = {
    ReqState.DONE: "done",
    ReqState.ESCALATED: "review",
}


# M14e/M15：state → stage 名（用于 stage_runs 表）。
STATE_TO_STAGE: dict[ReqState, str] = {
    ReqState.ANALYZING:              "analyze",
    ReqState.ANALYZE_ARTIFACT_CHECKING: "analyze_artifact_check",
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
# 仅以下 state 对应的 stage 由 BKD agent 跑；其他 state 是机械 checker / teardown，
# 没 BKD session 可绑。webhook 用此集合决定 stamp_bkd_session_id 是否值得调用。
AGENT_STAGES: frozenset[str] = frozenset({
    "analyze", "verifier", "fixer", "accept", "archive",
})

# event → stage_runs.outcome 标签。escalate / session.failed 全归 fail。
_EVENT_TO_OUTCOME: dict[Event, str] = {
    Event.ANALYZE_DONE:                "pass",
    Event.ANALYZE_ARTIFACT_CHECK_PASS: "pass",
    Event.ANALYZE_ARTIFACT_CHECK_FAIL: "fail",
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

    例外：(REVIEW_RUNNING, VERIFY_PASS) 是 transition 表声明的 self-loop，但
    apply_verify_pass action 内部手工 CAS 把 state 推到 target stage_running，那条手工
    CAS 绕过本函数。如果不在这里显式 close verifier 那条 run，stage_runs 会留 orphan
    （ended_at IS NULL），Q12/Q13 verifier 看板漏掉 PASS 决策。
    """
    if cur_state == ReqState.REVIEW_RUNNING and event == Event.VERIFY_PASS:
        try:
            await stage_runs.close_latest_stage_run(
                pool, req_id, "verifier", outcome="pass",
            )
        except Exception as e:
            log.warning("engine.stage_runs.write_failed",
                        req_id=req_id, cur=cur_state.value, nxt=next_state.value,
                        evt=event.value, error=str(e))
    if cur_state == next_state:
        return
    try:
        cur_stage = STATE_TO_STAGE.get(cur_state)
        if cur_stage:
            outcome = _EVENT_TO_OUTCOME.get(event, "cancelled")
            await stage_runs.close_latest_stage_run(
                pool, req_id, cur_stage,
                outcome=outcome,
                fail_reason=event.value if outcome != "pass" else None,
            )
        next_stage = STATE_TO_STAGE.get(next_state)
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


async def _sync_intent_status_on_terminal(
    project_id: str,
    intent_issue_id: str | None,
    terminal_state: ReqState,
    *,
    req_id: str | None = None,
) -> None:
    """REQ-bkd-hitl-end-to-end-loop-1777273753: 终态把 BKD intent issue 推到对的列。

    DONE → "done"；ESCALATED → "review"（让"待审查"列只剩需要人 follow-up 的）。
    幂等（statusId PATCH 是替换语义）。BKD 不可达不阻塞状态机：异常吞，仅 log
    warning —— `req_state.state` 是真相，BKD 看板只是 UX 镜像，落后一拍可人工修。

    intent_issue_id 缺失（罕见：测试 / 重放路径未填 ctx）→ no-op skip。
    """
    target = _TERMINAL_STATE_TO_BKD_STATUS_ID.get(terminal_state)
    if not target or not intent_issue_id:
        return
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            await bkd.update_issue(
                project_id=project_id,
                issue_id=intent_issue_id,
                status_id=target,
            )
    except Exception as e:
        log.warning(
            "engine.intent_status_sync_failed",
            req_id=req_id,
            intent_issue_id=intent_issue_id,
            target_status_id=target,
            error=str(e),
        )


async def _tag_intent_pr_ready(
    project_id: str,
    intent_issue_id: str | None,
    pr_urls: dict | None,
    *,
    req_id: str | None = None,
) -> None:
    """REQ-pr-ready-for-review-notify: REVIEW_RUNNING 进入时给 BKD intent issue 打 pr-ready tag.

    pr_urls 空或缺失 → no-op（PR 还没开，跳过标记）。
    intent_issue_id 缺失 → no-op。
    BKD 不可达 → log warning，不阻塞状态机。
    """
    if not intent_issue_id or not pr_urls:
        return
    add_tags = ["pr-ready"]
    for repo, url in pr_urls.items():
        # url 形如 https://github.com/owner/repo/pull/123 → tag pr:owner/repo#123
        try:
            pr_num = url.rstrip("/").split("/")[-1]
            tag = f"pr:{repo}#{pr_num}"
            if tag not in add_tags:
                add_tags.append(tag)
        except (AttributeError, IndexError):
            log.warning("engine.pr_ready.url_parse_failed", url=url, req_id=req_id)
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            await bkd.merge_tags_and_update(
                project_id=project_id,
                issue_id=intent_issue_id,
                add=add_tags,
            )
    except Exception as e:
        log.warning(
            "engine.pr_ready_tag_failed",
            req_id=req_id,
            intent_issue_id=intent_issue_id,
            error=str(e),
        )


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

    # M10：转 terminal state 时立即清 runner（fire-and-forget）。
    # 但跳过 terminal self-loop（cur 已是 terminal）—— 出现在 ESCALATED 接 verifier 续 follow-up
    # 的场景：engine 表面 self-loop，apply_verify_pass 内部把 state CAS 推到下游 stage_running
    # 并 ensure_runner；这时再清 pod 会误删 resume 路径刚拉起的 pod。
    if (transition.next_state in _TERMINAL_STATES
            and cur_state not in _TERMINAL_STATES):
        task = asyncio.create_task(
            _cleanup_runner_on_terminal(req_id, transition.next_state)
        )
        _cleanup_tasks.add(task)
        task.add_done_callback(_cleanup_tasks.discard)
        # REQ-bkd-hitl-end-to-end-loop-1777273753：同 cleanup 同步起一个
        # fire-and-forget task 把 BKD intent issue statusId 推到对应列
        # （DONE→done / ESCALATED→review）。intent_issue_id 由 webhook 在
        # 第一次见到该 REQ 时落进 ctx；缺失 → helper 自身 no-op skip。
        intent_issue_id = (ctx or {}).get("intent_issue_id")
        sync_task = asyncio.create_task(
            _sync_intent_status_on_terminal(
                project_id, intent_issue_id, transition.next_state,
                req_id=req_id,
            )
        )
        _cleanup_tasks.add(sync_task)
        sync_task.add_done_callback(_cleanup_tasks.discard)

    # REQ-pr-ready-for-review-notify: REVIEW_RUNNING 进入时给 BKD intent issue 打 pr-ready tag
    if transition.next_state == ReqState.REVIEW_RUNNING:
        intent_issue_id = (ctx or {}).get("intent_issue_id")
        pr_urls = (ctx or {}).get("pr_urls")
        pr_ready_task = asyncio.create_task(
            _tag_intent_pr_ready(
                project_id, intent_issue_id, pr_urls,
                req_id=req_id,
            )
        )
        _cleanup_tasks.add(pr_ready_task)
        pr_ready_task.add_done_callback(_cleanup_tasks.discard)

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
        log.exception(
            "engine.action_failed", action=action_name, error=str(e),
            error_type=type(e).__name__,
            traceback=traceback.format_exc(),
        )
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
    # 把失败原因暴露给 escalate action 当 reason —— key 必须是 escalated_reason
    # （原来写 action_escalate_reason，escalate.py 读 escalated_reason，永远读不到，
    # fallback 到 body.event 拿到 "issue.updated" 之类无意义值）。
    await req_state.update_context(pool, req_id, {
        "escalated_reason": f"action-error:{error_reason[:160]}",
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
