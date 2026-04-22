"""create_pr_ci_watch（v0.2 + M2 checker）：staging-test pass → 等 PR CI 全套绿。

feature flag checker_pr_ci_watch_enabled:
  False（默认）: 创建 BKD agent issue（老路，agent 用 gh CLI 轮询，报 tag）
  True: sisyphus 自己调 GitHub REST API 轮询 check-runs，emit PR_CI_PASS/FAIL/TIMEOUT

ctx 输入（checker 模式）：
  pr_repo: "owner/name" 例 "phona/ubox-crosser"
  pr_number: int
M3 会改成统一从 manifest.yaml 读，M2 先硬编码 ctx 字段（dev/staging-test agent 写入）。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..checkers import pr_ci_watch as checker
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import artifact_checks, db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("create_pr_ci_watch")
async def create_pr_ci_watch(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("pr-ci", Event.PR_CI_PASS, req_id=req_id):
        return rv

    if settings.checker_pr_ci_watch_enabled:
        return await _run_checker(req_id=req_id, ctx=ctx)

    return await _dispatch_bkd_agent(body=body, req_id=req_id, ctx=ctx)


# ── 新路：sisyphus 自检 ────────────────────────────────────────────────────

async def _run_checker(*, req_id: str, ctx: dict) -> dict:
    pr_repo = ctx.get("pr_repo")
    pr_number = ctx.get("pr_number")
    if not pr_repo or not pr_number:
        # ctx 缺字段：emit fail 进 bugfix（按 PR_CI_FAIL 路径走，跟老路报 pr-ci:fail tag 等价）
        log.error("create_pr_ci_watch.checker_missing_ctx",
                  req_id=req_id, pr_repo=pr_repo, pr_number=pr_number)
        return {
            "emit": Event.PR_CI_FAIL.value,
            "reason": "missing pr_repo / pr_number in ctx",
            "exit_code": -1,
        }

    log.info("create_pr_ci_watch.checker_path",
             req_id=req_id, repo=pr_repo, pr=pr_number)

    try:
        result = await checker.watch_pr_ci(
            repo=pr_repo,
            pr_number=int(pr_number),
            poll_interval_sec=settings.pr_ci_watch_poll_interval_sec,
            timeout_sec=settings.pr_ci_watch_timeout_sec,
        )
    except Exception as e:
        log.exception("create_pr_ci_watch.checker_error", req_id=req_id, error=str(e))
        return {
            "emit": Event.PR_CI_FAIL.value,
            "reason": str(e)[:200],
            "exit_code": -1,
        }

    pool = db.get_pool()
    await artifact_checks.insert_check(pool, req_id, "pr-ci-watch", result)

    if result.exit_code == 124:
        emit = Event.PR_CI_TIMEOUT
    elif result.passed:
        emit = Event.PR_CI_PASS
    else:
        emit = Event.PR_CI_FAIL

    log.info("create_pr_ci_watch.checker_done", req_id=req_id,
             emit=emit.value, exit_code=result.exit_code,
             duration_sec=round(result.duration_sec, 1))

    return {
        "emit": emit.value,
        "passed": result.passed,
        "exit_code": result.exit_code,
        "cmd": result.cmd,
        "duration_sec": result.duration_sec,
    }


# ── 老路：BKD agent（flag off 时走这里）────────────────────────────────────

async def _dispatch_bkd_agent(*, body, req_id: str, ctx: dict) -> dict:
    proj = body.projectId
    source_issue_id = body.issueId   # 上游 staging-test issue

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [pr-ci-watch]{short_title(ctx)}",
            tags=["pr-ci", req_id, f"parent-id:{source_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "pr_ci_watch.md.j2",
            req_id=req_id,
            source_issue_id=source_issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"pr_ci_watch_issue_id": issue.id})

    log.info("create_pr_ci_watch.bkd_agent_dispatched", req_id=req_id, pr_ci_issue=issue.id)
    return {"pr_ci_watch_issue_id": issue.id}
