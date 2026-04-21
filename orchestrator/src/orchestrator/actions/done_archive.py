"""done_archive: accept pass → openspec apply + 创 PR 收尾。"""
from __future__ import annotations

import json

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("done_archive")
async def done_archive(*, body, req_id, tags, ctx):
    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = (ctx or {}).get("workdir") or f"{settings.workdir_root}/feat-{req_id}"
    repo_url = (ctx or {}).get("repo_url") or _repo_url(proj)
    accept_issue_id = (ctx or {}).get("accept_issue_id") or body.issueId

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [DONE] archive & PR",
            tags=["done-archive", req_id, f"parent-id:{accept_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "done_archive.md.j2",
            req_id=req_id, branch=branch, workdir=workdir,
            repo_url=repo_url, accept_issue_id=accept_issue_id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"archive_issue_id": issue.id})

    log.info("done_archive.done", req_id=req_id, archive_issue=issue.id)
    return {"archive_issue_id": issue.id}


def _repo_url(project_id: str) -> str:
    try:
        return json.loads(settings.project_repo_map_json).get(project_id, "")
    except json.JSONDecodeError:
        return ""
