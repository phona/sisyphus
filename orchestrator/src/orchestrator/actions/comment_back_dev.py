"""comment_back_dev: ci-unit fail 轻量回炉 dev issue。

不算 bugfix round；评论原因 + push dev issue 回 working 让 agent 自修。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from . import register

log = structlog.get_logger(__name__)


@register("comment_back_dev")
async def comment_back_dev(*, body, req_id, tags, ctx):
    proj = body.projectId
    dev_issue_id = (ctx or {}).get("dev_issue_id")
    ci_issue_id = body.issueId
    if not dev_issue_id:
        log.error("comment_back.no_dev_issue", req_id=req_id, ctx=ctx)
        return {"error": "no dev_issue_id in ctx"}

    msg = (
        "🔴 CI 自检未过\n\n"
        f"REQ={req_id}\n"
        f"CI issue: {ci_issue_id}\n\n"
        "请查看 CI issue 的 `## CI Result` block，修复后 move review 重新触发。"
        "此为轻量反馈，不计入 bugfix round。"
    )

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.follow_up_issue(project_id=proj, issue_id=dev_issue_id, prompt=msg)
        await bkd.update_issue(project_id=proj, issue_id=dev_issue_id, status_id="working")

    log.info("comment_back.done", req_id=req_id, dev_issue=dev_issue_id, ci_issue=ci_issue_id)
    return {"dev_issue_id": dev_issue_id, "comment": "ci-unit-fail"}
