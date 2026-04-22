"""create_pr_ci_watch（v0.2）：staging-test pass → 下发 pr-ci-watch BKD agent。

前置：staging-test agent 已 push branch 到各 source repo 并 gh pr create，
manifest.yaml.pr_by_repo + sha_by_repo 应已填好。

pr-ci-watch agent 职责：
- 读 manifest.sources + pr_by_repo + sha_by_repo
- 对每个 (repo, sha) 轮询 GitHub commit statuses（gh CLI）
- 全绿 → 解析 image-publish status description 写 manifest.image_tags，贴 pr-ci:pass
- 任一红 → 贴 pr-ci:fail
- 超时（默认 30 min）→ 贴 pr-ci:timeout
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("create_pr_ci_watch")
async def create_pr_ci_watch(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("pr-ci", Event.PR_CI_PASS, req_id=req_id):
        return rv

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

    log.info("create_pr_ci_watch.done", req_id=req_id, pr_ci_issue=issue.id)
    return {"pr_ci_watch_issue_id": issue.id}
