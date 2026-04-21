"""create_accept: ci-int 通过后派 accept-agent 跑 AI-QA。"""
from __future__ import annotations

import json

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("create_accept")
async def create_accept(*, body, req_id, tags, ctx):
    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = f"{settings.workdir_root}/accept-{req_id}"
    repo_url = (ctx or {}).get("repo_url") or _repo_url(proj)
    source_issue_id = body.issueId  # 触发的 ci-int issue

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [ACCEPT] AI-QA",
            tags=["accept", req_id, f"parent-id:{source_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "accept.md.j2",
            req_id=req_id, branch=branch, workdir=workdir,
            repo_url=repo_url, source_issue_id=source_issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"accept_issue_id": issue.id})

    log.info("create_accept.done", req_id=req_id, accept_issue=issue.id)
    return {"accept_issue_id": issue.id}


def _repo_url(project_id: str) -> str:
    try:
        return json.loads(settings.project_repo_map_json).get(project_id, "")
    except json.JSONDecodeError:
        return ""
