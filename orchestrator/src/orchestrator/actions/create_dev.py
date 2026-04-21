"""create_dev: SPG 通过后开 dev issue（开发主链）。"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("create_dev")
async def create_dev(*, body, req_id, tags, ctx):
    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = (ctx or {}).get("workdir") or f"{settings.workdir_root}/feat-{req_id}"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [DEV]",
            tags=["dev", req_id],
            status_id="todo",
        )
        prompt = render(
            "dev.md.j2",
            req_id=req_id, branch=branch, workdir=workdir,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "dev_issue_id": issue.id,
        "branch": branch,
        "workdir": workdir,
    })

    log.info("create_dev.done", req_id=req_id, dev_issue=issue.id)
    return {"dev_issue_id": issue.id}
