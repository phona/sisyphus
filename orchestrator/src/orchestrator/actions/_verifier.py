"""M14b/M14c：verifier-agent 框架

每个 stage transition（success / fail）调 `invoke_verifier` 起一个 BKD verifier-agent
issue，让它做主观判断 —— 3 路决策：**pass / fix / escalate**。verifier 完成后
webhook.py 解析 decision JSON，映射成 Event 推状态机。

本模块只管"起 issue + 挂 prompt"：同步返回 verifier_issue_id，不等决策
（异步走 session.completed webhook）。决策 → Event 映射在 webhook.py。

同时提供 action handler：
- `apply_verify_pass`：decision=pass → 手工 CAS 回 stage_running + 链式 emit
   对应 stage 的 done/pass 事件（走原主链 transition）
- `start_fixer`：decision=fix → 起 fixer agent（dev / spec）
- `invoke_verifier_after_fix`：fixer 完 → 再调 verifier 复查
- `invoke_verifier_for_staging_test_fail` / `_pr_ci_fail` / `_accept_fail`：
   机械 checker / accept fail 的 3 个专门入口。stage 由 transition table 写死，
   不再从 webhook tags sniff（机械 checker 没 issue，tags 来自上游 dev issue，
   以前按 tag 推会把 staging-test fail 误路成 dev）。

砍掉 retry_checker：基础设施 flaky / 抖动直接 escalate 给人介入，sisyphus 不再机制性
兜 retry —— 避免假阳性 retry 死循环 + 跟"薄编排，不抢 AI 决定权"哲学一致。

M14c：verifier_enabled 默认 True，旧 fail_kind / bugfix 子链已砍。
"""
from __future__ import annotations

from typing import Literal

import structlog

from .. import k8s_runner
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event, ReqState
from ..store import db, req_state
from . import register, short_title

log = structlog.get_logger(__name__)


# 支持的 stage 名（对应 prompts/verifier/{stage}_{trigger}.md.j2）
# 包括 agent stage（analyze）和 checker stage（spec_lint / dev_cross_check / staging_test / pr_ci）
_STAGES = {"analyze", "spec_lint", "challenger", "dev_cross_check", "staging_test", "pr_ci", "accept"}

# Trigger 类型
Trigger = Literal["success", "fail"]

# stage → decision=pass 时要 emit 的原主链 event + 目标 stage_running state
# 用于 apply_verify_pass 手工把 state 从 REVIEW_RUNNING 回推到对应 stage_running，
# 随后链式 emit 该 stage 的 done/pass 事件走原 transition。
_PASS_ROUTING: dict[str, tuple[ReqState, Event]] = {
    "analyze":           (ReqState.ANALYZING,                Event.ANALYZE_DONE),
    "spec_lint":         (ReqState.SPEC_LINT_RUNNING,        Event.SPEC_LINT_PASS),
    "challenger":        (ReqState.CHALLENGER_RUNNING,       Event.CHALLENGER_PASS),
    "dev_cross_check":   (ReqState.DEV_CROSS_CHECK_RUNNING,  Event.DEV_CROSS_CHECK_PASS),
    "staging_test":      (ReqState.STAGING_TEST_RUNNING,     Event.STAGING_TEST_PASS),
    "pr_ci":             (ReqState.PR_CI_RUNNING,            Event.PR_CI_PASS),
    "accept":            (ReqState.ACCEPT_RUNNING,           Event.ACCEPT_PASS),
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
        project_id=project_id,
        project_alias=project_id,
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
            model=settings.agent_model,
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

def _stage_from_tags_or_ctx(tags: list[str] | None, ctx: dict | None) -> str | None:
    """从触发本次 transition 的 issue tags 取 stage（verify:<stage>），fallback ctx。

    多 verifier 并发时 ctx.verifier_stage 会被后来者覆盖，issue tag 是无歧义真相。
    """
    for t in (tags or []):
        if t.startswith("verify:"):
            return t.removeprefix("verify:")
    return (ctx or {}).get("verifier_stage")


@register("apply_verify_pass", idempotent=True)
async def apply_verify_pass(*, body, req_id, tags, ctx):
    """decision=pass：读 verifier issue 的 verify:<stage> tag，手工 CAS REVIEW_RUNNING
    → stage_running，链式 emit 该 stage 的 done/pass 事件（走原主链 transition）。
    """
    stage = _stage_from_tags_or_ctx(tags, ctx)
    route = _PASS_ROUTING.get(stage) if stage else None
    if route is None:
        log.error("apply_verify_pass.unknown_stage", req_id=req_id, stage=stage)
        return {"emit": Event.VERIFY_ESCALATE.value,
                "reason": f"unknown verifier_stage: {stage!r}"}

    target_state, next_event = route
    pool = db.get_pool()
    # CAS 接 REVIEW_RUNNING（正常 verifier 完成）+ ESCALATED（人续 escalate 的 verifier）
    src_state = None
    for src in (ReqState.REVIEW_RUNNING, ReqState.ESCALATED):
        if await req_state.cas_transition(
            pool, req_id, src, target_state,
            Event.VERIFY_PASS, "apply_verify_pass",
        ):
            src_state = src
            break
    if src_state is None:
        log.warning("apply_verify_pass.cas_failed", req_id=req_id, stage=stage)
        return {"cas_failed": True}

    # 推下一 stage 前 ensure_runner（idempotent，pod 在则秒返）。
    # 关键场景：从 ESCALATED 续 → escalate 时 runner pod 被删了；fixer 跑完续也可能没 pod。
    # PVC 因 #40 retain，workspace 状态不丢。
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        log.warning("apply_verify_pass.no_runner_controller", req_id=req_id)
    else:
        pod = await rc.ensure_runner(req_id, wait_ready=True)
        log.info("apply_verify_pass.runner_ready",
                 req_id=req_id, stage=stage, pod=pod, src_state=src_state.value)

    log.info("apply_verify_pass.done",
             req_id=req_id, stage=stage,
             src_state=src_state.value, target_state=target_state.value,
             emit=next_event.value)
    return {"emit": next_event.value, "stage": stage}


@register("start_fixer", idempotent=False)
async def start_fixer(*, body, req_id, tags, ctx):
    """decision=fix：起对应 fixer agent（dev / spec）。

    ctx 里应有 verifier 之前写的 fixer / scope（webhook 解 decision 时存）。
    本期的 prompt 先用通用 bugfix 模板兜底，PR4 / 独立 PR 再做专用 fixer prompt。

    stage 优先从当前 verifier issue 的 tags 取（`verify:<stage>`）—— 多 verifier 并发
    时 ctx.verifier_stage 可能被后来者覆盖，从触发本次 transition 的 issue tag 直读
    更稳。

    硬 cap 防 verifier↔fixer 死循环：
      ctx.fixer_round 是"已起过的 round 数"。本次将起的是 next_round = current + 1。
      next_round > settings.fixer_round_cap 时不再起 fixer，emit VERIFY_ESCALATE 走
      标准 escalate（reason=fixer-round-cap，escalate.py 识别为 hard reason，不会被
      auto-resume 绕过）。
    """
    proj = body.projectId
    ctx = ctx or {}
    stage = None
    for t in (tags or []):
        if t.startswith("verify:"):
            stage = t.removeprefix("verify:")
            break
    if not stage:
        stage = ctx.get("verifier_stage")
    fixer = ctx.get("verifier_fixer") or "dev"
    scope = ctx.get("verifier_scope") or ""
    reason = ctx.get("verifier_reason") or ""
    branch = ctx.get("branch") or f"feat/{req_id}"

    pool = db.get_pool()

    # ─── round cap：第 N+1 次 start_fixer 直接 escalate（不起 fixer）───────
    current_round = int(ctx.get("fixer_round") or 0)
    next_round = current_round + 1
    cap = settings.fixer_round_cap
    if next_round > cap:
        await req_state.update_context(pool, req_id, {
            "escalated_reason": "fixer-round-cap",
            "fixer_round_cap_hit": cap,
        })
        log.warning(
            "start_fixer.round_cap_exceeded",
            req_id=req_id, stage=stage, fixer=fixer,
            current_round=current_round, cap=cap,
        )
        return {
            "emit": Event.VERIFY_ESCALATE.value,
            "reason": "fixer-round-cap",
            "fixer_round": current_round,
            "cap": cap,
        }

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
                f"round:{next_round}",
            ],
            status_id="todo",
            use_worktree=True,
            model=settings.agent_model,
        )
        # 通用 bugfix prompt 作为过渡；PR4 再做每类 fixer 专用模板。
        prompt = render(
            "bugfix.md.j2",
            req_id=req_id, round_n=next_round,
            kind=f"verifier-{fixer}",
            source_issue_id=ctx.get("verifier_issue_id", ""),
            branch=branch,
            workdir=f"{settings.workdir_root}/feat-{req_id}",
            project_id=proj,
            project_alias=proj,
        )
        # 把 verifier 的 scope / reason 叠进 prompt 作为上下文
        if scope or reason:
            prompt += f"\n\n## Verifier 决策\n- fixer: {fixer}\n- scope: {scope}\n- reason: {reason}\n"
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    await req_state.update_context(pool, req_id, {
        "fixer_issue_id": issue.id,
        "fixer_role": fixer,
        "fixer_scope": scope,
        "fixer_round": next_round,
    })

    log.info("start_fixer.done",
             req_id=req_id, fixer=fixer, stage=stage, issue_id=issue.id,
             round=next_round, cap=cap)
    return {
        "fixer_issue_id": issue.id, "fixer": fixer, "stage": stage,
        "fixer_round": next_round,
    }


@register("invoke_verifier_after_fix", idempotent=False)
async def invoke_verifier_after_fix(*, body, req_id, tags, ctx):
    """fixer 完 → 再跑 verifier 一次（同 stage，trigger=success：fixer 已改过代码）。

    stage 必须从**当前 fixer issue 的 tags** 取（`parent-stage:<stage>`），不能依赖
    ctx.verifier_stage —— 多 verifier 并发时 ctx 是最新一个的 stage，老 fixer 完成时
    ctx 已被覆盖，会拿错 stage。fixer issue 自带 parent-stage tag 是无歧义的真相。
    """
    ctx = ctx or {}
    stage = None
    for t in (tags or []):
        if t.startswith("parent-stage:"):
            stage = t.removeprefix("parent-stage:")
            break
    if not stage:
        stage = ctx.get("verifier_stage") or "dev_cross_check"
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


@register("invoke_verifier_for_challenger_fail", idempotent=False)
async def invoke_verifier_for_challenger_fail(*, body, req_id, tags, ctx):
    """CHALLENGER_FAIL (M18) → 起 verifier-agent(stage=challenger, trigger=fail)。

    challenger 拒写 contract test 通常意味着 spec 自相矛盾 / 缺关键定义 —— verifier
    判要不要回头让 spec_fixer 修 spec 还是 escalate 给 user。
    """
    return await _invoke_verifier_fail(
        stage="challenger", body=body, req_id=req_id, ctx=ctx,
    )
