"""M14b/M14c：verifier-agent 框架

每个 stage transition（success / fail）调 `invoke_verifier` 起一个 BKD verifier-agent
issue，让它做主观判断（pass / fix / retry_checker / escalate）。verifier 完成后
webhook.py 解析 decision JSON，映射成 Event 推状态机。

本模块只管"起 issue + 挂 prompt"：同步返回 verifier_issue_id，不等决策
（异步走 session.completed webhook）。决策 → Event 映射在 webhook.py。

同时提供 action handler：
- `apply_verify_pass`：decision=pass → 手工 CAS 回 stage_running + 链式 emit
   对应 stage 的 done/pass 事件（走原主链 transition）
- `apply_verify_retry_checker`：decision=retry_checker → 同手法，回 stage_running
   + 链 emit "restart" 事件触发 checker 重跑
- `start_fixer`：decision=fix → 起 fixer agent（dev / spec）
- `invoke_verifier_after_fix`：fixer 完 → 再调 verifier 复查
- `invoke_verifier_for_staging_test_fail` / `_pr_ci_fail` / `_accept_fail`：
   机械 checker / accept fail 的 3 个专门入口。stage 由 transition table 写死，
   不再从 webhook tags sniff（机械 checker 没 issue，tags 来自上游 dev issue，
   以前按 tag 推会把 staging-test fail 误路成 dev）。

M14c：verifier_enabled 默认 True，旧 fail_kind / bugfix 子链已砍。
"""
from __future__ import annotations

from typing import Literal

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event, ReqState
from ..store import db, req_state
from . import register, short_title

log = structlog.get_logger(__name__)


# 支持的 stage 名（对应 prompts/verifier/{stage}_{trigger}.md.j2）
# 包括 agent stage（analyze）和 checker stage（spec_lint / dev_cross_check / staging_test / pr_ci）
_STAGES = {"analyze", "spec_lint", "dev_cross_check", "staging_test", "pr_ci", "accept"}

# Trigger 类型
Trigger = Literal["success", "fail"]

# stage → decision=pass 时要 emit 的原主链 event + 目标 stage_running state
# 用于 apply_verify_pass 手工把 state 从 REVIEW_RUNNING 回推到对应 stage_running，
# 随后链式 emit 该 stage 的 done/pass 事件走原 transition。
_PASS_ROUTING: dict[str, tuple[ReqState, Event]] = {
    "analyze":           (ReqState.ANALYZING,                Event.ANALYZE_DONE),
    "spec_lint":         (ReqState.SPEC_LINT_RUNNING,        Event.SPEC_LINT_PASS),
    "dev_cross_check":   (ReqState.DEV_CROSS_CHECK_RUNNING,  Event.DEV_CROSS_CHECK_PASS),
    "staging_test":      (ReqState.STAGING_TEST_RUNNING,     Event.STAGING_TEST_PASS),
    "pr_ci":             (ReqState.PR_CI_RUNNING,            Event.PR_CI_PASS),
    "accept":            (ReqState.ACCEPT_RUNNING,           Event.ACCEPT_PASS),
}

# stage → retry_checker 时回推的 state（checker 类 stage 才真有意义）
_RETRY_TARGET_STATE: dict[str, ReqState] = {
    "analyze":          ReqState.ANALYZING,
    "spec_lint":        ReqState.SPEC_LINT_RUNNING,
    "dev_cross_check":  ReqState.DEV_CROSS_CHECK_RUNNING,
    "staging_test":     ReqState.STAGING_TEST_RUNNING,
    "pr_ci":            ReqState.PR_CI_RUNNING,
    "accept":           ReqState.ACCEPT_RUNNING,
}


# ─── invoke_verifier：起 BKD verifier issue ──────────────────────────────

async def invoke_verifier(
    *,
    stage: str,
    trigger: Trigger,
    req_id: str,
    project_id: str,
    artifact_paths: list[str] | None = None,
    stderr_tail: str | None = None,
    history: list[dict] | None = None,
    ctx: dict | None = None,
) -> dict:
    """起一个 BKD verifier-agent issue，异步等 session.completed 推进状态机。

    Args:
        stage: 被审阶段名（analyze/spec/dev/staging_test/pr_ci/accept）
        trigger: "success"=机械 checker 过 / agent 跑完；"fail"=checker 红 / agent 报错
        req_id / project_id: 绑定 REQ
        artifact_paths: 可选，给 prompt 提示 agent 要看哪些产物（spec / 日志）
        stderr_tail: fail 触发时的 stderr 尾部
        history: 可选，之前 verifier / fixer 轮次摘要

    Returns:
        {"verifier_issue_id": "<id>", "stage": stage, "trigger": trigger}
    """
    if stage not in _STAGES:
        raise ValueError(f"unknown verifier stage: {stage!r}")
    if trigger not in ("success", "fail"):
        raise ValueError(f"trigger must be 'success' or 'fail', got {trigger!r}")

    template_name = f"verifier/{stage}_{trigger}.md.j2"
    prompt = render(
        template_name,
        req_id=req_id,
        stage=stage,
        trigger=trigger,
        artifact_paths=artifact_paths or [],
        stderr_tail=stderr_tail or "",
        history=history or [],
    )

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=project_id,
            title=f"[{req_id}] [VERIFY {stage}] {trigger}{short_title(ctx)}",
            tags=[
                "verifier",
                req_id,
                f"verify:{stage}",
                f"trigger:{trigger}",
            ],
            status_id="todo",
            use_worktree=True,   # 并行 verifier 互不抢 working tree
        )
        await bkd.follow_up_issue(project_id=project_id, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=project_id, issue_id=issue.id, status_id="working")

    # 落 ctx 给 apply_verify_* action 后续查 stage 用
    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "verifier_issue_id": issue.id,
        "verifier_stage": stage,
        "verifier_trigger": trigger,
    })

    log.info(
        "verifier.invoked",
        req_id=req_id, stage=stage, trigger=trigger, issue_id=issue.id,
    )
    return {
        "verifier_issue_id": issue.id,
        "stage": stage,
        "trigger": trigger,
    }


# ─── action handlers ────────────────────────────────────────────────────

@register("apply_verify_pass", idempotent=True)
async def apply_verify_pass(*, body, req_id, tags, ctx):
    """decision=pass：读 ctx.verifier_stage，手工 CAS REVIEW_RUNNING → stage_running，
    链式 emit 该 stage 的 done/pass 事件（走原主链 transition，推进到下一 stage）。
    """
    stage = (ctx or {}).get("verifier_stage")
    route = _PASS_ROUTING.get(stage) if stage else None
    if route is None:
        log.error("apply_verify_pass.unknown_stage", req_id=req_id, stage=stage)
        return {"emit": Event.VERIFY_ESCALATE.value,
                "reason": f"unknown verifier_stage: {stage!r}"}

    target_state, next_event = route
    pool = db.get_pool()
    advanced = await req_state.cas_transition(
        pool, req_id, ReqState.REVIEW_RUNNING, target_state,
        Event.VERIFY_PASS, "apply_verify_pass",
    )
    if not advanced:
        # 状态已被并发事件改动 → 让上层 skip，不重复 emit
        log.warning("apply_verify_pass.cas_failed", req_id=req_id, stage=stage)
        return {"cas_failed": True}

    log.info("apply_verify_pass.done",
             req_id=req_id, stage=stage,
             target_state=target_state.value, emit=next_event.value)
    return {"emit": next_event.value, "stage": stage}


@register("apply_verify_retry_checker", idempotent=True)
async def apply_verify_retry_checker(*, body, req_id, tags, ctx):
    """decision=retry_checker：回到 stage_running 状态。PR3 之后由 stage action
    自己处理"当前在 stage_running 再触发一次 checker" 的语义（或通过 ctx flag）。
    本期先只把 state 回滚并落 ctx 标记，等真正接入时再完善。
    """
    stage = (ctx or {}).get("verifier_stage")
    target = _RETRY_TARGET_STATE.get(stage) if stage else None
    if target is None:
        log.error("apply_verify_retry_checker.unknown_stage",
                  req_id=req_id, stage=stage)
        return {"emit": Event.VERIFY_ESCALATE.value,
                "reason": f"unknown verifier_stage: {stage!r}"}

    pool = db.get_pool()
    advanced = await req_state.cas_transition(
        pool, req_id, ReqState.REVIEW_RUNNING, target,
        Event.VERIFY_RETRY_CHECKER, "apply_verify_retry_checker",
    )
    if not advanced:
        log.warning("apply_verify_retry_checker.cas_failed",
                    req_id=req_id, stage=stage)
        return {"cas_failed": True}

    await req_state.update_context(pool, req_id, {
        "retry_checker_pending": True,
        "retry_checker_stage": stage,
    })
    log.info("apply_verify_retry_checker.done",
             req_id=req_id, stage=stage, target_state=target.value)
    return {"retry_checker": True, "stage": stage}


@register("start_fixer", idempotent=False)
async def start_fixer(*, body, req_id, tags, ctx):
    """decision=fix：起对应 fixer agent（dev / spec）。

    ctx 里应有 verifier 之前写的 fixer / scope（webhook 解 decision 时存）。
    本期的 prompt 先用通用 bugfix 模板兜底，PR4 / 独立 PR 再做专用 fixer prompt。
    """
    proj = body.projectId
    ctx = ctx or {}
    stage = ctx.get("verifier_stage")
    fixer = ctx.get("verifier_fixer") or "dev"
    scope = ctx.get("verifier_scope") or ""
    reason = ctx.get("verifier_reason") or ""
    branch = ctx.get("branch") or f"feat/{req_id}"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [FIXER {fixer}] {stage}{short_title(ctx)}",
            tags=[
                "fixer",
                req_id,
                f"fixer:{fixer}",
                f"parent-stage:{stage}",
                f"parent-id:{ctx.get('verifier_issue_id', '')}",
            ],
            status_id="todo",
            use_worktree=True,
        )
        # 通用 bugfix prompt 作为过渡；PR4 再做每类 fixer 专用模板。
        prompt = render(
            "bugfix.md.j2",
            req_id=req_id, round_n=ctx.get("fixer_round", 1),
            kind=f"verifier-{fixer}",
            source_issue_id=ctx.get("verifier_issue_id", ""),
            branch=branch,
            workdir=f"{settings.workdir_root}/feat-{req_id}",
        )
        # 把 verifier 的 scope / reason 叠进 prompt 作为上下文
        if scope or reason:
            prompt += f"\n\n## Verifier 决策\n- fixer: {fixer}\n- scope: {scope}\n- reason: {reason}\n"
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "fixer_issue_id": issue.id,
        "fixer_role": fixer,
        "fixer_scope": scope,
    })

    log.info("start_fixer.done",
             req_id=req_id, fixer=fixer, stage=stage, issue_id=issue.id)
    return {"fixer_issue_id": issue.id, "fixer": fixer, "stage": stage}


@register("invoke_verifier_after_fix", idempotent=False)
async def invoke_verifier_after_fix(*, body, req_id, tags, ctx):
    """fixer 完 → 再跑 verifier 一次（同 stage，trigger=success：fixer 已改过代码）。"""
    ctx = ctx or {}
    stage = ctx.get("verifier_stage") or "dev"
    history = [
        *(ctx.get("verifier_history") or []),
        {
            "fixer": ctx.get("fixer_role"),
            "fixer_issue_id": ctx.get("fixer_issue_id"),
        },
    ]

    result = await invoke_verifier(
        stage=stage,
        trigger="success",
        req_id=req_id,
        project_id=body.projectId,
        history=history,
        ctx=ctx,
    )
    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"verifier_history": history})
    return result


async def _invoke_verifier_fail(*, stage: str, body, req_id, ctx):
    """统一跑 invoke_verifier(trigger=fail)。stage 由调用方写死。"""
    return await invoke_verifier(
        stage=stage,
        trigger="fail",
        req_id=req_id,
        project_id=body.projectId,
        ctx=ctx,
    )


@register("invoke_verifier_for_staging_test_fail", idempotent=False)
async def invoke_verifier_for_staging_test_fail(*, body, req_id, tags, ctx):
    """STAGING_TEST_FAIL → 起 verifier-agent(stage=staging_test, trigger=fail)。

    stage 来自 transition table，不从 tags 推。
    （机械 checker 没自己的 BKD issue，webhook tags 来自上游 dev issue，
    以前 sniff tag 会把 staging-test fail 误路成 dev。）
    """
    return await _invoke_verifier_fail(
        stage="staging_test", body=body, req_id=req_id, ctx=ctx,
    )


@register("invoke_verifier_for_pr_ci_fail", idempotent=False)
async def invoke_verifier_for_pr_ci_fail(*, body, req_id, tags, ctx):
    """PR_CI_FAIL → 起 verifier-agent(stage=pr_ci, trigger=fail)。"""
    return await _invoke_verifier_fail(
        stage="pr_ci", body=body, req_id=req_id, ctx=ctx,
    )


@register("invoke_verifier_for_accept_fail", idempotent=False)
async def invoke_verifier_for_accept_fail(*, body, req_id, tags, ctx):
    """TEARDOWN_DONE_FAIL → 起 verifier-agent(stage=accept, trigger=fail)。"""
    return await _invoke_verifier_fail(
        stage="accept", body=body, req_id=req_id, ctx=ctx,
    )


@register("invoke_verifier_for_spec_lint_fail", idempotent=False)
async def invoke_verifier_for_spec_lint_fail(*, body, req_id, tags, ctx):
    """SPEC_LINT_FAIL → 起 verifier-agent(stage=spec_lint, trigger=fail)。"""
    return await _invoke_verifier_fail(
        stage="spec_lint", body=body, req_id=req_id, ctx=ctx,
    )


@register("invoke_verifier_for_dev_cross_check_fail", idempotent=False)
async def invoke_verifier_for_dev_cross_check_fail(*, body, req_id, tags, ctx):
    """DEV_CROSS_CHECK_FAIL → 起 verifier-agent(stage=dev_cross_check, trigger=fail)。"""
    return await _invoke_verifier_fail(
        stage="dev_cross_check", body=body, req_id=req_id, ctx=ctx,
    )
