"""done_archive: accept pass → openspec apply + 创 PR 收尾。"""
from __future__ import annotations

import structlog

from .. import pr_links
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("done_archive", idempotent=False)  # 创建新 archive issue + 开 PR
async def done_archive(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("archive", Event.ARCHIVE_DONE, req_id=req_id):
        return rv
    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = (ctx or {}).get("workdir") or f"{settings.workdir_root}/feat-{req_id}"
    accept_issue_id = (ctx or {}).get("accept_issue_id") or body.issueId
    # REQ-pr-issue-traceability-1777218612: pre-render the known PR list so
    # the archive-agent has clickable links pinned in its prompt without
    # re-running `gh pr list` in the runner.
    pr_urls = (ctx or {}).get("pr_urls") or {}

    # PR-link tag 注入（REQ-issue-link-pr-quality-base-1777218242）
    links = await pr_links.ensure_pr_links_in_ctx(
        req_id=req_id, branch=branch, ctx=ctx, project_id=proj,
    )
    extra_tags = pr_links.pr_link_tags(links)

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [DONE] archive & PR{short_title(ctx)}",
            tags=["done-archive", req_id, f"parent-id:{accept_issue_id}", *extra_tags],
            status_id="todo",
            model=settings.agent_model,
        )
        prompt = render(
            "done_archive.md.j2",
            req_id=req_id, branch=branch, workdir=workdir,
            accept_issue_id=accept_issue_id,
            project_id=proj,
            project_alias=proj,
            pr_urls=pr_urls,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"archive_issue_id": issue.id})

    log.info("done_archive.done", req_id=req_id, archive_issue=issue.id)
    return {"archive_issue_id": issue.id}
