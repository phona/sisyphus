"""open_gh_and_bugfix: ci-int 或 accept 失败时双路开 issue。

Policy（沿用 d444f91 的策略 A）：
- 一定开 GitHub incident issue（让人评审）
- 同时开 BKD bugfix issue（让 dev-fix-agent 自修）
- bugfix round 超过 CB_THRESHOLD（=3）就熔断，只开 GH 不开 bugfix
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)

CB_THRESHOLD = 3


@register("open_gh_and_bugfix")
async def open_gh_and_bugfix(*, body, req_id, tags, ctx):
    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = (ctx or {}).get("workdir") or f"{settings.workdir_root}/feat-{req_id}"
    repo_url = (ctx or {}).get("repo_url") or settings.repo_url
    source_issue_id = body.issueId
    kind = _infer_kind(tags)
    incident_key = f"{req_id}:{kind}"

    # bugfix round = 已存在 bugfix issue 数 + 1
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        all_issues = await bkd.list_issues(proj, limit=200)
        existing_bug = [
            i for i in all_issues
            if "bugfix" in i.tags and req_id in i.tags
        ]
        round_n = len(existing_bug) + 1
        should_bugfix = round_n <= CB_THRESHOLD

        # 1. open GH incident issue
        gh = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [GH-ISSUE] {kind}",
            tags=["github-incident", req_id, f"kind:{kind}", f"incident:{incident_key}"],
            status_id="todo",
        )
        gh_prompt = render(
            "github_issue.md.j2",
            req_id=req_id, kind=kind, source_issue_id=source_issue_id,
            branch=branch, workdir=workdir, repo_url=repo_url,
            incident_key=incident_key,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=gh.id, prompt=gh_prompt)
        await bkd.update_issue(project_id=proj, issue_id=gh.id, status_id="working")

        bug_issue_id: str | None = None
        if should_bugfix:
            # 2. open BKD bugfix issue（dev 侧）
            bug = await bkd.create_issue(
                project_id=proj,
                title=f"[{req_id}] [BUGFIX round-{round_n}] {kind}",
                tags=["bugfix", req_id, f"round-{round_n}", f"parent-id:{source_issue_id}"],
                status_id="todo",
            )
            bug_prompt = render(
                "bugfix.md.j2",
                req_id=req_id, round_n=round_n, kind=kind,
                source_issue_id=source_issue_id, branch=branch,
                workdir=f"{settings.workdir_root}/bugfix-dev-{req_id}-round-{round_n}",
                repo_url=repo_url,
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


def _infer_kind(tags: list[str]) -> str:
    if "accept" in tags:
        return "accept-fail"
    if "ci" in tags:
        return "ci-integration-fail"
    return "unknown-fail"
