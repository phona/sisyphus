"""M14b/M14c：verifier-agent 框架

每个 stage transition（success / fail）调 `invoke_verifier` 起一个 BKD verifier-agent
issue，让它做主观判断 —— 3 路决策：**pass / fix / escalate**。verifier 完成后
webhook.py 解析 decision JSON，映射成 Event 推状态机。

本模块只管"起 issue + 挂 prompt"：同步返回 verifier_issue_id，不等决策
（异步走 session.completed webhook）。决策 → Event 映射在 router.py。

同时提供 action handler：
- `start_fixer`：decision=fix → 起 fixer agent（dev / spec）
- `invoke_verifier_after_fix`：fixer 完 → 再调 verifier 复查
- `invoke_verifier_for_staging_test_fail` / `_pr_ci_fail` / `_accept_fail`：
   机械 checker / accept fail 的 3 个专门入口。stage 由 transition table 写死，
   不再从 webhook tags sniff（机械 checker 没 issue，tags 来自上游 dev issue，
   以前按 tag 推会把 staging-test fail 误路成 dev）。

砍掉 retry_checker：基础设施 flaky / 抖动直接 escalate 给人介入，sisyphus 不再机制性
兜 retry —— 避免假阳性 retry 死循环 + 跟"薄编排，不抢 AI 决定权"哲学一致。

M14c：verifier_enabled 默认 True，旧 fail_kind / bugfix 子链已砍。

REQ-refactor-verify-pass-transition-1777727230：
apply_verify_pass 已删，decision=pass 由 router 译成对应主链 pass 事件
（如 STAGING_TEST_PASS），transition 表显式写死 REVIEW_RUNNING → next_state。
"""
from __future__ import annotations

from typing import Literal

import structlog

from .. import pr_links
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event, ReqState
from ..store import artifact_checks, db, dispatch_slugs, req_state, stage_runs
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

# stage → decision=retry 时要 CAS 回的 stage_running state + 重新 dispatch 的 create action 名。
# 只覆盖机械 checker stage（runner pod kubectl-exec / GHA 轮询），不含 analyze/accept/challenger。
_RETRY_ROUTING: dict[str, tuple[ReqState, str]] = {
    "staging_test":    (ReqState.STAGING_TEST_RUNNING,    "create_staging_test"),
    "dev_cross_check": (ReqState.DEV_CROSS_CHECK_RUNNING, "create_dev_cross_check"),
    "spec_lint":       (ReqState.SPEC_LINT_RUNNING,       "create_spec_lint"),
    "pr_ci":           (ReqState.PR_CI_RUNNING,           "create_pr_ci_watch"),
}

# prompt template stage 名 → artifact_checks.stage DB 值
_STAGE_TO_DB: dict[str, str] = {
    "spec_lint":              "spec-lint",
    "dev_cross_check":        "dev-cross-check",
    "staging_test":           "staging-test",
    "pr_ci":                  "pr-ci-watch",
    "analyze_artifact_check": "analyze-artifact-check",
}

# stdout/stderr 各保留最后多少行，避免 prompt 过长
_CHECKER_TAIL_LINES = 50


def _tail_lines(text: str | None, n: int = _CHECKER_TAIL_LINES) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[-n:])
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

    fixer_round = (ctx or {}).get("fixer_round", 0)
    slug = f"verifier|{req_id}|{stage}|{trigger}|r{fixer_round}"
    pool = db.get_pool()
    # Clear stale verifier_issue_id from prior round (#324, same pattern as #316).
    # invoke_verifier 创建新 verifier issue 期间 watchdog 可能基于 ctx 里残留的
    # 上轮 stale verifier_issue_id (early-ended session) 误判 watchdog_stuck →
    # 强制 escalate 把 in-flight invoke_verifier 打断。入口先清，让 watchdog
    # 走 line 310 defense-in-depth (issue_id is None and stuck_sec is None) skip。
    if ctx and ctx.get("verifier_issue_id"):
        await req_state.update_context(pool, req_id, {"verifier_issue_id": None})
    if hit := await dispatch_slugs.get(pool, slug):
        log.info("invoke_verifier.slug_hit", req_id=req_id, slug=slug, issue_id=hit)
        await req_state.update_context(pool, req_id, {
            "verifier_issue_id": hit,
            "verifier_stage": stage,
            "verifier_trigger": trigger,
            "verifier_parse_retry_count": 0,
        })
        return {"verifier_issue_id": hit, "stage": stage, "trigger": trigger}

    checker_stdout = ""
    checker_stderr = ""
    checker_exit_code = None
    if trigger == "fail":
        db_stage = _STAGE_TO_DB.get(stage)
        if db_stage:
            pool = db.get_pool()
            row = await artifact_checks.get_latest(pool, req_id, db_stage)
            if row:
                checker_stdout = _tail_lines(row.get("stdout_tail") or "")
                checker_stderr = _tail_lines(row.get("stderr_tail") or "")
                checker_exit_code = row.get("exit_code")

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
        checker_stdout=checker_stdout,
        checker_stderr=checker_stderr,
        checker_exit_code=checker_exit_code,
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

    slug = f"fixer|{req_id}|{fixer}|r{next_round}"
    if hit := await dispatch_slugs.get(pool, slug):
        log.info("start_fixer.slug_hit", req_id=req_id, slug=slug, issue_id=hit)
        await req_state.update_context(pool, req_id, {
            "fixer_issue_id": hit,
            "fixer_role": fixer,
            "fixer_scope": scope,
            "fixer_round": next_round,
        })
        return {"fixer_issue_id": hit, "fixer": fixer, "stage": stage, "fixer_round": next_round}

    # PR-link tag 注入（REQ-issue-link-pr-quality-base-1777218242）
    branch_for_links = (ctx or {}).get("branch") or f"feat/{req_id}"
    links = await pr_links.ensure_pr_links_in_ctx(
        req_id=req_id, branch=branch_for_links, ctx=ctx, project_id=proj,
    )
    extra_tags = pr_links.pr_link_tags(links)

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
                *extra_tags,
            ],
            status_id="todo",
            use_worktree=True,
            model=settings.agent_model,
        )
        # 通用 bugfix prompt 作为过渡；PR4 再做每类 fixer 专用模板。
        # REQ-base-branch-override-1777480690: forward base branch info so fixer
        # lint uses the correct merge-base.
        prompt = render(
            "bugfix.md.j2",
            req_id=req_id, round_n=next_round,
            kind=f"verifier-{fixer}",
            source_issue_id=ctx.get("verifier_issue_id", ""),
            branch=branch,
            workdir=f"{settings.workdir_root}/feat-{req_id}",
            project_id=proj,
            project_alias=proj,
            base_branch=ctx.get("base_branch"),
            base_branches=ctx.get("base_branches") or {},
        )
        # 把 verifier 的 scope / reason 叠进 prompt 作为上下文
        if scope or reason:
            prompt += f"\n\n## Verifier 决策\n- fixer: {fixer}\n- scope: {scope}\n- reason: {reason}\n"
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    await dispatch_slugs.put(pool, slug, issue.id)
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
    情况是 agent 写了 spec 漏了 proposal/tasks → 可判 fix + fixer=spec。
    """
    return await _invoke_verifier_fail(
        stage="analyze_artifact_check", body=body, req_id=req_id, ctx=ctx,
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


@register("apply_verify_infra_retry", idempotent=True)
async def apply_verify_infra_retry(*, body, req_id, tags, ctx):
    """decision=retry：verifier 判定 infra-flake → 有界重跑 stage checker。

    从 ctx.infra_retry_count 读已重跑次数：
    - < settings.verifier_infra_retry_cap → 递增计数，CAS REVIEW_RUNNING → {stage}_RUNNING，
      close verifier stage_run，再调对应 create_* action 重跑 checker。
    - >= cap → emit VERIFY_ESCALATE（reason=infra-retry-cap）让人介入。

    仅覆盖机械 checker stage（staging_test / dev_cross_check / spec_lint / pr_ci）。
    其他 stage（analyze / accept / challenger）输出 retry 时也走 escalate，并 log warning。
    """
    from . import REGISTRY  # 延迟导入避免循环

    stage = _stage_from_tags_or_ctx(tags, ctx)
    retry_info = _RETRY_ROUTING.get(stage) if stage else None

    if retry_info is None:
        log.warning(
            "apply_verify_infra_retry.stage_not_retryable",
            req_id=req_id, stage=stage,
        )
        return {
            "emit": Event.VERIFY_ESCALATE.value,
            "reason": f"stage not infra-retryable: {stage!r}",
        }

    target_state, create_action_name = retry_info
    ctx = ctx or {}
    pool = db.get_pool()
    retry_count = int(ctx.get("infra_retry_count") or 0)
    cap = settings.verifier_infra_retry_cap

    if retry_count >= cap:
        await req_state.update_context(pool, req_id, {
            "escalated_reason": "infra-retry-cap",
            "infra_retry_cap_hit": cap,
        })
        log.warning(
            "apply_verify_infra_retry.cap_exceeded",
            req_id=req_id, stage=stage, count=retry_count, cap=cap,
        )
        return {
            "emit": Event.VERIFY_ESCALATE.value,
            "reason": "infra-retry-cap",
            "infra_retry_count": retry_count,
            "cap": cap,
        }

    # CAS REVIEW_RUNNING → target_state（重回 stage_running 再跑一次 checker）
    cas_ok = await req_state.cas_transition(
        pool, req_id, ReqState.REVIEW_RUNNING, target_state,
        Event.VERIFY_INFRA_RETRY, "apply_verify_infra_retry",
    )
    if not cas_ok:
        log.warning("apply_verify_infra_retry.cas_failed", req_id=req_id, stage=stage)
        return {"cas_failed": True}

    # close verifier stage_run（离开 REVIEW_RUNNING 时收尾）
    try:
        await stage_runs.close_latest_stage_run(
            pool, req_id, "verifier", outcome="infra-retry",
        )
    except Exception as exc:
        log.warning("apply_verify_infra_retry.stage_runs.close_failed",
                    req_id=req_id, error=str(exc))

    # 递增计数（先 CAS 成功再写，避免 cap 计数超发）
    new_count = retry_count + 1
    await req_state.update_context(pool, req_id, {"infra_retry_count": new_count})

    # 调对应 create 函数重跑 checker
    create_fn = REGISTRY.get(create_action_name)
    if create_fn is None:
        log.error("apply_verify_infra_retry.missing_create_action",
                  req_id=req_id, action=create_action_name)
        return {"emit": Event.VERIFY_ESCALATE.value, "reason": f"missing action: {create_action_name}"}

    log.info(
        "apply_verify_infra_retry.dispatching",
        req_id=req_id, stage=stage, new_count=new_count, cap=cap,
        create_action=create_action_name,
    )
    return await create_fn(body=body, req_id=req_id, tags=tags, ctx=ctx)
