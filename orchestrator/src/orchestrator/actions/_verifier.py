"""M14b/M14c：verifier-agent 框架

每个 stage transition（success / fail）调 `invoke_verifier` 起一个 BKD verifier-agent
issue，让它做主观判断 —— 3 路决策：**pass / retry-analyze / escalate**。verifier 完成后
webhook.py 解析 decision JSON，映射成 Event 推状态机。

本模块只管"起 issue + 挂 prompt"：同步返回 verifier_issue_id，不等决策
（异步走 session.completed webhook）。决策 → Event 映射在 webhook.py。

同时提供 action handler：
- `apply_verify_pass`：decision=pass → 手工 CAS 回 stage_running + 链式 emit
   对应 stage 的 done/pass 事件（走原主链 transition）
- `apply_verify_retry_analyze`：decision=retry-analyze → 打回 analyze 重新跑
- `invoke_verifier_for_staging_test_fail` / `_pr_ci_fail` / `_accept_fail`：
   机械 checker / accept fail 的 3 个专门入口。stage 由 transition table 写死，
   不再从 webhook tags sniff（机械 checker 没 issue，tags 来自上游 dev issue，
   以前按 tag 推会把 staging-test fail 误路成 dev）。

M14c：verifier_enabled 默认 True，旧 fail_kind / bugfix 子链已砍。
"""
from __future__ import annotations

from typing import Literal

import structlog

from .. import k8s_runner, pr_links
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event, ReqState
from ..store import db, dispatch_slugs, req_state, stage_runs
from . import register, short_title

log = structlog.get_logger(__name__)


# 支持的 stage 名（对应 prompts/verifier/{stage}_{trigger}.md.j2）
# 包括 agent stage（analyze）和 checker stage（spec_lint / dev_cross_check / staging_test / pr_ci）
_STAGES = {
    "analyze", "analyze_artifact_check", "spec_lint", "challenger",
    "dev_cross_check", "staging_test", "pr_ci", "accept",
}

# Trigger 类型
Trigger = Literal["success", "fail"]

# stage → decision=pass 时要 emit 的原主链 event + 目标 stage_running state
# 用于 apply_verify_pass 手工把 state 从 REVIEW_RUNNING 回推到对应 stage_running，
# 随后链式 emit 该 stage 的 done/pass 事件走原 transition。
_PASS_ROUTING: dict[str, tuple[ReqState, Event]] = {
    "analyze":                 (ReqState.ANALYZING,                Event.ANALYZE_DONE),
    "analyze_artifact_check":  (ReqState.ANALYZE_ARTIFACT_CHECKING,
                                Event.ANALYZE_ARTIFACT_CHECK_PASS),
    "spec_lint":               (ReqState.SPEC_LINT_RUNNING,        Event.SPEC_LINT_PASS),
    "challenger":              (ReqState.CHALLENGER_RUNNING,       Event.CHALLENGER_PASS),
    "dev_cross_check":         (ReqState.DEV_CROSS_CHECK_RUNNING,  Event.DEV_CROSS_CHECK_PASS),
    "staging_test":            (ReqState.STAGING_TEST_RUNNING,     Event.STAGING_TEST_PASS),
    "pr_ci":                   (ReqState.PR_CI_RUNNING,            Event.PR_CI_PASS),
    "accept":                  (ReqState.ACCEPT_RUNNING,           Event.ACCEPT_PASS),
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
        history: 可选，之前 verifier 轮次摘要

    Returns:
        {"verifier_issue_id": "<id>", "stage": stage, "trigger": trigger}
    """
    if stage not in _STAGES:
        raise ValueError(f"unknown verifier stage: {stage!r}")
    if trigger not in ("success", "fail"):
        raise ValueError(f"trigger must be 'success' or 'fail', got {trigger!r}")

    retry_count = int((ctx or {}).get("retry_analyze_count", 0))
    slug = f"verifier|{req_id}|{stage}|{trigger}|r{retry_count}"
    pool = db.get_pool()
    if hit := await dispatch_slugs.get(pool, slug):
        log.info("invoke_verifier.slug_hit", req_id=req_id, slug=slug, issue_id=hit)
        await req_state.update_context(pool, req_id, {
            "verifier_issue_id": hit,
            "verifier_stage": stage,
            "verifier_trigger": trigger,
            "verifier_parse_retry_count": 0,
        })
        return {"verifier_issue_id": hit, "stage": stage, "trigger": trigger}

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

    # PR-link tag 注入（REQ-issue-link-pr-quality-base-1777218242）：
    # verifier issue 在 dev 之后才创建，PR 已存在 → 第一次成功 discover 时
    # 同时回填 ctx 里 analyze_issue_id 等已有 sisyphus issue 的 tag。
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    links = await pr_links.ensure_pr_links_in_ctx(
        req_id=req_id, branch=branch, ctx=ctx, project_id=project_id,
    )
    extra_tags = pr_links.pr_link_tags(links)

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=project_id,
            title=f"[{req_id}] [VERIFY {stage}] {trigger}{short_title(ctx)}",
            tags=[
                "verifier",
                req_id,
                f"verify:{stage}",
                f"trigger:{trigger}",
                *extra_tags,
            ],
            status_id="todo",
            use_worktree=True,   # 并行 verifier 互不抢 working tree
            model=settings.agent_model,
        )
        await bkd.follow_up_issue(project_id=project_id, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=project_id, issue_id=issue.id, status_id="working")

    # 落 ctx 给 apply_verify_* action 后续查 stage 用；pool 已在 slug 检查前取得
    await dispatch_slugs.put(pool, slug, issue.id)
    await req_state.update_context(pool, req_id, {
        "verifier_issue_id": issue.id,
        "verifier_stage": stage,
        "verifier_trigger": trigger,
        "verifier_parse_retry_count": 0,
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

    # 收尾 stage_runs.verifier 行（best-effort）。
    # transition (REVIEW_RUNNING, VERIFY_PASS) -> REVIEW_RUNNING 是 self-loop —
    # engine._record_stage_transitions 看到 cur==next 直接 return，**不写**
    # stage_runs；这里手 CAS 推到 target_state 也绕过 engine 的钩子。结果是
    # verifier 行 insert 后没人 close（DB 实证：18 条 outcome=NULL，污染
    # Metabase 的 stage_stats / agent_quality 看板）。
    # 仅 src=REVIEW_RUNNING 路径需要 close（ESCALATED 续是 self-loop 重入，
    # 上一轮 verifier 行已被前次 escalate 路径关掉，不会有新的 open verifier 行）。
    if src_state == ReqState.REVIEW_RUNNING:
        try:
            await stage_runs.close_latest_stage_run(
                pool, req_id, "verifier", outcome="pass",
            )
        except Exception as e:
            log.warning("apply_verify_pass.stage_runs.close_failed",
                        req_id=req_id, error=str(e))

    # 推下一 stage 前 ensure_runner（idempotent，pod 在则秒返）。
    # 关键场景：从 ESCALATED 续 → escalate 时 runner pod 被删了；retry-analyze 续也可能没 pod。
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


@register("apply_verify_retry_analyze", idempotent=True)
async def apply_verify_retry_analyze(*, body, req_id, tags, ctx):
    """decision=retry-analyze：手工 CAS REVIEW_RUNNING → ANALYZING，然后起 analyze agent。

    从 ctx.intent_issue_id 取 intent issue 作为 analyze 入口，复用 start_analyze
    拉起 runner + clone + dispatch analyze prompt。

    CAS source 同时接 REVIEW_RUNNING（正常 verifier 完成）+ ESCALATED（人续 escalate
    的 verifier）。
    """
    ctx = ctx or {}
    stage = _stage_from_tags_or_ctx(tags, ctx) or "unknown"
    pool = db.get_pool()

    # CAS 接 REVIEW_RUNNING + ESCALATED
    src_state = None
    for src in (ReqState.REVIEW_RUNNING, ReqState.ESCALATED):
        if await req_state.cas_transition(
            pool, req_id, src, ReqState.ANALYZING,
            Event.VERIFY_RETRY_ANALYZE, "apply_verify_retry_analyze",
        ):
            src_state = src
            break
    if src_state is None:
        log.warning("apply_verify_retry_analyze.cas_failed", req_id=req_id)
        return {"cas_failed": True}

    # 收尾 stage_runs.verifier 行
    if src_state == ReqState.REVIEW_RUNNING:
        try:
            await stage_runs.close_latest_stage_run(
                pool, req_id, "verifier", outcome="retry-analyze",
            )
        except Exception as e:
            log.warning("apply_verify_retry_analyze.stage_runs.close_failed",
                        req_id=req_id, error=str(e))

    # 推下一 stage 前 ensure_runner（idempotent，pod 在则秒返）。
    # 关键场景：从 ESCALATED 续 → escalate 时 runner pod 被删了。
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        log.warning("apply_verify_retry_analyze.no_runner_controller", req_id=req_id)
    else:
        pod = await rc.ensure_runner(req_id, wait_ready=True)
        log.info("apply_verify_retry_analyze.runner_ready",
                 req_id=req_id, pod=pod, src_state=src_state.value)

    # 把 retry-analyze 上下文写进 ctx，让 analyze agent 知道这是 retry
    retry_count = int(ctx.get("retry_analyze_count", 0)) + 1
    await req_state.update_context(pool, req_id, {
        "retry_analyze_reason": ctx.get("verifier_reason", ""),
        "retry_analyze_stage": stage,
        "retry_analyze_count": retry_count,
    })

    # 起 analyze agent：用 intent issue 作为 body
    intent_issue_id = ctx.get("intent_issue_id")
    if not intent_issue_id:
        log.error("apply_verify_retry_analyze.no_intent_issue", req_id=req_id)
        return {"emit": Event.VERIFY_ESCALATE.value,
                "reason": "no intent_issue_id in ctx"}

    # 构造 synthetic body 调 start_analyze
    synthetic_body = type("B", (), {
        "projectId": body.projectId,
        "issueId": intent_issue_id,
    })()

    from . import REGISTRY  # 延迟导入避免循环
    start_analyze_fn = REGISTRY.get("start_analyze")
    if start_analyze_fn is None:
        log.error("apply_verify_retry_analyze.start_analyze_not_registered", req_id=req_id)
        return {"emit": Event.VERIFY_ESCALATE.value,
                "reason": "start_analyze not registered"}

    log.info("apply_verify_retry_analyze.dispatching_analyze",
             req_id=req_id, stage=stage, retry_count=retry_count,
             intent_issue_id=intent_issue_id)
    result = await start_analyze_fn(
        body=synthetic_body, req_id=req_id, tags=tags, ctx=ctx,
    )
    return {"start_analyze_result": result}


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

    REQ-staging-test-baseline-diff-1777343371：ctx.staging_test_stderr_tail
    由 create_staging_test._run_checker 写入，含 baseline diff 上下文；
    透传给 verifier prompt 让 verifier 区分 "agent 引入的 fail" vs "main 上本来就坏"。
    """
    ctx = ctx or {}
    stderr_tail = ctx.get("staging_test_stderr_tail") or ""
    return await invoke_verifier(
        stage="staging_test",
        trigger="fail",
        req_id=req_id,
        project_id=body.projectId,
        stderr_tail=stderr_tail,
        ctx=ctx,
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


@register("invoke_verifier_for_analyze_artifact_check_fail", idempotent=False)
async def invoke_verifier_for_analyze_artifact_check_fail(*, body, req_id, tags, ctx):
    """ANALYZE_ARTIFACT_CHECK_FAIL → 起 verifier-agent(stage=analyze_artifact_check, trigger=fail)。

    REQ-analyze-artifact-check-1777254586：analyze 产物结构性校验失败。verifier
    通常应判 escalate（agent 自报 pass 但产物缺失，是 LLM 抽风类失败），少数
    情况是 agent 写了 spec 漏了 proposal/tasks → 可判 retry-analyze。
    """
    return await _invoke_verifier_fail(
        stage="analyze_artifact_check", body=body, req_id=req_id, ctx=ctx,
    )


@register("invoke_verifier_for_challenger_fail", idempotent=False)
async def invoke_verifier_for_challenger_fail(*, body, req_id, tags, ctx):
    """CHALLENGER_FAIL (M18) → 起 verifier-agent(stage=challenger, trigger=fail)。

    challenger 拒写 contract test 通常意味着 spec 自相矛盾 / 缺关键定义 —— verifier
    判 retry-analyze（回 analyze 修 spec）还是 escalate 给 user。
    """
    return await _invoke_verifier_fail(
        stage="challenger", body=body, req_id=req_id, ctx=ctx,
    )


