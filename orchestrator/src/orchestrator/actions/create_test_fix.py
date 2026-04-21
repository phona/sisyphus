"""create_test_fix: bugfix 完成 → 派 test-fix-agent（对抗审视测试）。"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("create_test_fix")
async def create_test_fix(*, body, req_id, tags, ctx):
    proj = body.projectId
    round_n = (ctx or {}).get("bugfix_round") or 1
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    source_issue_id = body.issueId  # 触发的 bugfix issue
    workdir = f"{settings.workdir_root}/bugfix-test-{req_id}-round-{round_n}"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [TEST-FIX round-{round_n}] adversarial review",
            tags=["test-fix", req_id, f"round-{round_n}", f"parent-id:{source_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "test_fix.md.j2",
            req_id=req_id, round_n=round_n,
            source_issue_id=source_issue_id, branch=branch,
            workdir=workdir,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"test_fix_issue_id": issue.id})

    log.info("create_test_fix.done", req_id=req_id, round=round_n, issue=issue.id)
    return {"test_fix_issue_id": issue.id, "round": round_n}
