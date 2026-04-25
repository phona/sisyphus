"""create_pr_ci_watch（v0.2 + M2 checker + M15）：
staging-test pass → 等 PR CI 全套绿。

feature flag checker_pr_ci_watch_enabled:
  False（默认）: 创建 BKD agent issue（老路，agent 用 gh CLI 轮询，报 tag）
  True: sisyphus 自己调 GitHub REST API 轮询 check-runs，emit PR_CI_PASS/FAIL/TIMEOUT

repo 列表来源（M15 哲学：runner 是真理，不维护额外 metadata）：
  1. runner pod `/workspace/source/*/` discovery（start_analyze 已 server-side
     clone 好，跟其它 checker 走同一条契约 —— staging_test / dev_cross_check
     都遍历这个目录）
  2. ctx.intake_finalized_intent.involved_repos / ctx.involved_repos
     （intake 阶段已知 / 直接 analyze 路径透传）

REQ-clone-and-pr-ci-fallback-1777115925 把第三档 process-global env fallback
删了 —— stale 全局值在多 REQ / 多仓场景下注定指错仓。两个 source 都空时
_run_checker 把 repos=None 透传给 watch_pr_ci，由 checker 抛 ValueError，本
action 翻译成 PR_CI_TIMEOUT（直接 ESCALATED 不进 verifier）。
"""
from __future__ import annotations

import re

import structlog

from .. import k8s_runner
from ..bkd import BKDClient
from ..checkers import pr_ci_watch as checker
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import artifact_checks, db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

# git@github.com:owner/repo(.git) 或 https://github.com/owner/repo(.git)
_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$")


@register("create_pr_ci_watch", idempotent=False)
async def create_pr_ci_watch(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("pr-ci", Event.PR_CI_PASS, req_id=req_id):
        return rv

    if settings.checker_pr_ci_watch_enabled:
        return await _run_checker(req_id=req_id, ctx=ctx or {})

    return await _dispatch_bkd_agent(body=body, req_id=req_id, ctx=ctx)


# ── 新路：sisyphus 自检 ────────────────────────────────────────────────────

async def _discover_repos_from_runner(req_id: str) -> list[str]:
    """ls /workspace/source/*/ + git remote → ['owner/repo', ...]，失败返 []。"""
    cmd = (
        "for d in /workspace/source/*/; do "
        "  [ -d \"$d/.git\" ] && git -C \"$d\" remote get-url origin 2>/dev/null; "
        "done"
    )
    try:
        rc = k8s_runner.get_controller()
        result = await rc.exec_in_runner(req_id, cmd, timeout_sec=30)
    except Exception as e:
        log.warning("create_pr_ci_watch.runner_discovery_failed",
                    req_id=req_id, error=str(e))
        return []

    repos: list[str] = []
    for line in result.stdout.splitlines():
        m = _REMOTE_RE.search(line.strip())
        if m:
            repos.append(m.group(1))
    log.info("create_pr_ci_watch.runner_discovered", req_id=req_id, repos=repos)
    return repos


async def _run_checker(*, req_id: str, ctx: dict) -> dict:
    log.info("create_pr_ci_watch.checker_path", req_id=req_id)
    branch = ctx.get("branch") or f"feat/{req_id}"

    # repo 来源优先级：runner 文件系统（M15 真理）> ctx involved_repos
    # （process-global env fallback 已删，REQ-clone-and-pr-ci-fallback-1777115925）
    repos = await _discover_repos_from_runner(req_id)
    if not repos:
        finalized = ctx.get("intake_finalized_intent") or {}
        repos = finalized.get("involved_repos") or ctx.get("involved_repos")

    try:
        result = await checker.watch_pr_ci(
            req_id,
            branch=branch,
            poll_interval_sec=settings.pr_ci_watch_poll_interval_sec,
            timeout_sec=settings.pr_ci_watch_timeout_sec,
            repos=repos,
        )
    except ValueError as e:
        # Config error → ESCALATED directly (PR_CI_TIMEOUT), not verifier (PR_CI_FAIL).
        log.error("create_pr_ci_watch.config_error", req_id=req_id, error=str(e))
        return {
            "emit": Event.PR_CI_TIMEOUT.value,
            "reason": f"config error: {e}"[:200],
            "exit_code": -1,
        }
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
            model=settings.agent_model,
        )
        prompt = render(
            "pr_ci_watch.md.j2",
            req_id=req_id,
            source_issue_id=source_issue_id,
            project_id=proj,
            project_alias=proj,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"pr_ci_watch_issue_id": issue.id})

    log.info("create_pr_ci_watch.bkd_agent_dispatched", req_id=req_id, pr_ci_issue=issue.id)
    return {"pr_ci_watch_issue_id": issue.id}
