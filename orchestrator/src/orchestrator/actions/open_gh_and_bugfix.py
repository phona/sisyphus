"""open_gh_and_bugfix：staging / pr-ci / accept 失败 → 开 GH incident + 单 dev-fix bugfix。

M5：砍 DFIX+TFIX 双链（不再 fanout 出 test-fix，也不再跑 reviewer 裁判）。本 action
只起一个 dev-fix agent。bugfix round 计数以 ctx["bugfix_round"] 为准（M4 retry policy
写入）；缺省回退 BKD list-issues 兜底，让 M4 未到位也能单独跑。

Circuit breaker：同一 REQ 累计 round 超 CB_THRESHOLD 只开 GH issue 不开 bugfix。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register, short_title

log = structlog.get_logger(__name__)

CB_THRESHOLD = 3


@register("open_gh_and_bugfix", idempotent=False)  # 创建 GH issue + bugfix BKD issue
async def open_gh_and_bugfix(*, body, req_id, tags, ctx):
    proj = body.projectId
    ctx = ctx or {}
    branch = ctx.get("branch") or f"feat/{req_id}"
    source_issue_id = body.issueId
    kind = _infer_kind(tags)
    incident_key = f"{req_id}:{kind}"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        round_n = await _next_round(bkd, proj, req_id, ctx)
        should_bugfix = round_n <= CB_THRESHOLD

        # 1. 开 GH incident issue（审计 + 人工兜底）
        gh = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [GH-ISSUE] {kind}{short_title(ctx)}",
            tags=["github-incident", req_id, f"kind:{kind}", f"incident:{incident_key}"],
            status_id="todo",
        )
        gh_prompt = render(
            "github_issue.md.j2",
            req_id=req_id, kind=kind, source_issue_id=source_issue_id,
            branch=branch, workdir=f"{settings.workdir_root}/feat-{req_id}",
            incident_key=incident_key,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=gh.id, prompt=gh_prompt)
        await bkd.update_issue(project_id=proj, issue_id=gh.id, status_id="working")

        # 2. 开单 dev-fix bugfix（熔断后只开 GH）
        bug_issue_id: str | None = None
        if should_bugfix:
            bug = await bkd.create_issue(
                project_id=proj,
                title=f"[{req_id}] [BUGFIX round-{round_n}] {kind}{short_title(ctx)}",
                tags=["bugfix", req_id, f"round-{round_n}", f"parent-id:{source_issue_id}"],
                status_id="todo",
            )
            bug_prompt = render(
                "bugfix.md.j2",
                req_id=req_id, round_n=round_n, kind=kind,
                source_issue_id=source_issue_id, branch=branch,
                workdir=f"{settings.workdir_root}/bugfix-dev-{req_id}-round-{round_n}",
            )
            await bkd.follow_up_issue(project_id=proj, issue_id=bug.id, prompt=bug_prompt)
            await bkd.update_issue(project_id=proj, issue_id=bug.id, status_id="working")
            bug_issue_id = bug.id

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "gh_incident_issue_id": gh.id,
        "bugfix_issue_id": bug_issue_id,
        "bugfix_round": round_n,
        "circuit_broken": not should_bugfix,
    })

    log.warning(
        "open_gh_and_bugfix.done",
        req_id=req_id, kind=kind, round=round_n,
        gh_issue=gh.id, bugfix_issue=bug_issue_id,
        circuit_broken=not should_bugfix,
    )
    return {
        "gh_issue_id": gh.id,
        "bugfix_issue_id": bug_issue_id,
        "round": round_n,
        "circuit_broken": not should_bugfix,
    }


async def _next_round(bkd, proj: str, req_id: str, ctx: dict) -> int:
    """Round counter 来源：优先 ctx（M4 retry policy 写） → 回退 BKD list-issues 数数。"""
    cur = ctx.get("bugfix_round")
    if isinstance(cur, int) and cur > 0:
        return cur + 1
    all_issues = await bkd.list_issues(proj, limit=200)
    existing = [i for i in all_issues if "bugfix" in i.tags and req_id in i.tags]
    return len(existing) + 1


def _infer_kind(tags: list[str]) -> str:
    if "accept" in tags:
        return "accept-fail"
    if "ci" in tags:
        return "ci-integration-fail"
    return "unknown-fail"
