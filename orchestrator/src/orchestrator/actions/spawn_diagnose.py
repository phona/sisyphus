"""spawn_diagnose：bugfix 反复失败（M4 retry policy 判定）→ 上一个轻 agent 分流。

职责：读历史 bugfix issue + 最近失败栈，写 diagnosis tag（code-bug / spec-bug /
env-bug / unknown）到本 issue → move review。router 见 tag 推对应 Event：
- diagnosis:code-bug → BUGFIX_RETRY → 再起 dev-fix
- diagnosis:spec-bug → SPEC_REWORK → escalate（spec-fix 本期不做）
- diagnosis:env-bug / unknown → BUGFIX_ENV_BUG → escalate
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..store import db, req_state
from . import register, short_title

log = structlog.get_logger(__name__)


@register("spawn_diagnose", idempotent=False)  # 创建新 diagnose BKD issue
async def spawn_diagnose(*, body, req_id, tags, ctx):
    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = f"{settings.workdir_root}/diagnose-{req_id}"
    source_issue_id = body.issueId
    bugfix_round = (ctx or {}).get("bugfix_round") or 0

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [DIAGNOSE after round-{bugfix_round}] triage{short_title(ctx)}",
            tags=["diagnose", req_id, f"parent-id:{source_issue_id}"],
            status_id="todo",
        )
        prompt = render(
            "diagnose.md.j2",
            req_id=req_id,
            round_n=bugfix_round,
            source_issue_id=source_issue_id,
            branch=branch,
            workdir=workdir,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"diagnose_issue_id": issue.id})

    log.info("spawn_diagnose.done", req_id=req_id, round=bugfix_round, issue=issue.id)
    return {"diagnose_issue_id": issue.id, "round": bugfix_round}
