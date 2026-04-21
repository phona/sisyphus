"""start_analyze: intent:analyze 入口。

行为（沿用旧 [ANZ] block 三步）：
1. update-issue 把 intent issue 改名 [REQ-xxx] [ANALYZE] xxx + tags=[analyze, REQ-xxx]
2. follow-up-issue 发 analyze prompt
3. update-issue statusId=working 触发 agent
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from . import register

log = structlog.get_logger(__name__)


@register("start_analyze")
async def start_analyze(*, body, req_id, tags, ctx):
    proj = body.projectId
    issue_id = body.issueId
    # intent 触发时 title 是用户输入的原文（无 [REQ-xxx] [ANALYZE] 前缀）
    raw_title = body.title or ""

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        # 1. 改 title + tags
        await bkd.update_issue(
            project_id=proj,
            issue_id=issue_id,
            title=f"[{req_id}] [ANALYZE] {raw_title}",
            tags=["analyze", req_id],
        )
        # 2. 发 prompt
        prompt = render("analyze.md.j2", req_id=req_id, repo_url=settings.repo_url)
        await bkd.follow_up_issue(project_id=proj, issue_id=issue_id, prompt=prompt)
        # 3. 推 working
        await bkd.update_issue(project_id=proj, issue_id=issue_id, status_id="working")

    log.info("start_analyze.done", req_id=req_id, issue_id=issue_id)
    return {"issue_id": issue_id, "req_id": req_id}
