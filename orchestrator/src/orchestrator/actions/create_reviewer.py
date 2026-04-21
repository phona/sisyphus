"""create_reviewer: test-fix 完成 → 派 reviewer-agent 选胜者 merge。"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("create_reviewer")
async def create_reviewer(*, body, req_id, tags, ctx):
    proj = body.projectId
    round_n = (ctx or {}).get("bugfix_round") or 1
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = f"{settings.workdir_root}/reviewer-{req_id}-round-{round_n}"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [REVIEWER round-{round_n}] pick winner",
            tags=["reviewer", req_id, f"round-{round_n}"],
            status_id="todo",
        )
        prompt = render(
            "reviewer.md.j2",
            req_id=req_id, round_n=round_n,
            branch=branch, workdir=workdir,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"reviewer_issue_id": issue.id})

    log.info("create_reviewer.done", req_id=req_id, round=round_n, issue=issue.id)
    return {"reviewer_issue_id": issue.id, "round": round_n}
