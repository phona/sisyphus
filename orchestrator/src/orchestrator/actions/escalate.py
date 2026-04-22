"""escalate: 终态卡住兜底。

只做两件事：
1. 在 intent issue 上加 `escalated` + `reason:<event>` tag（人工告警入口）
2. 落 ctx 标记 escalated_reason

不开新 issue（避免污染列表）；不 cancel 当前 issue（让人工有现场）。
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)


@register("escalate", idempotent=True)
async def escalate(*, body, req_id, tags, ctx):
    proj = body.projectId
    intent_issue_id = (ctx or {}).get("intent_issue_id") or body.issueId
    reason = (body.event or "unknown").replace(".", "-")[:40]

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        try:
            await bkd.merge_tags_and_update(
                proj, intent_issue_id,
                add=["escalated", f"reason:{reason}"],
            )
        except Exception as e:
            log.warning("escalate.tag_failed", req_id=req_id, error=str(e))

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "escalated_reason": reason,
        "escalated_source_issue_id": body.issueId,
    })

    log.warning("escalate.done", req_id=req_id, reason=reason, issue_id=intent_issue_id)
    return {"escalated": True, "reason": reason}
