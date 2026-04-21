"""create_accept: ci-int 通过后派 accept-agent 跑 AI-QA。

settings.skip_accept=True 时直接 emit accept.pass，不调 BKD agent。
用于 ttpos-arch-lab 集成完成前先测整链路（dev → done）。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title

log = structlog.get_logger(__name__)


@register("create_accept")
async def create_accept(*, body, req_id, tags, ctx):
    if settings.skip_accept:
        log.warning("create_accept.skipped", req_id=req_id, reason="SISYPHUS_SKIP_ACCEPT=true")
        # 直接驱动状态机进 archiving；context 标记跳过供 done_archive 知晓
        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {"accept_skipped": True})
        return {"skipped": True, "emit": Event.ACCEPT_PASS.value}

    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = f"{settings.workdir_root}/accept-{req_id}"
    source_issue_id = body.issueId  # 触发的 ci-int issue

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [ACCEPT] AI-QA{short_title(ctx)}",
            tags=["accept", req_id, f"parent-id:{source_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "accept.md.j2",
            req_id=req_id, branch=branch, workdir=workdir,
            source_issue_id=source_issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"accept_issue_id": issue.id})

    log.info("create_accept.done", req_id=req_id, accept_issue=issue.id)
    return {"accept_issue_id": issue.id}
