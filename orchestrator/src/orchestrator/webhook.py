"""Webhook handler：BKD → 状态机 → action dispatch。

唯一入口。/bkd-events 收 session.completed/failed，/bkd-issue-updated 收 issue.updated。
内部 decide+CAS+dispatch 走 engine.step（让 action 也能链式 emit 事件）。
"""
from __future__ import annotations

import hmac
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from . import engine
from . import observability as obs
from . import router as router_lib
from .bkd import BKDClient
from .config import settings
from .state import Event
from .store import db, dedup, req_state

log = structlog.get_logger(__name__)
api = APIRouter()


def _verify_token(x_sisyphus_token: str | None = Header(default=None)) -> None:
    """共享 token 校验。常量时间比较防 timing。"""
    expected = settings.webhook_token
    provided = x_sisyphus_token or ""
    if not expected or not hmac.compare_digest(expected, provided):
        log.warning("webhook.auth_failed", has_header=bool(x_sisyphus_token))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Sisyphus-Token",
        )


class WebhookBody(BaseModel):
    """BKD webhook payload（issue.updated 和 session.completed/failed 共用大部分字段）。"""
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event: str
    timestamp: str | None = None
    issueId: str = Field(..., alias="issueId")
    issueNumber: int | None = None
    projectId: str
    title: str | None = None
    tags: list[str] | None = None  # session events 不一定带，需要时 get-issue 补
    executionId: str | None = None
    finalStatus: str | None = None
    changes: dict[str, Any] | None = None  # issue.updated 携带


@api.post("/bkd-events")
@api.post("/bkd-issue-updated")
async def webhook(
    body: WebhookBody,
    x_sisyphus_token: str | None = Header(default=None),
) -> dict:
    _verify_token(x_sisyphus_token)
    pool = db.get_pool()

    # ─── 1. Dedup ───────────────────────────────────────────────────────────
    eid_parts = [body.timestamp or "", body.issueId, body.event]
    if body.executionId:
        eid_parts.append(body.executionId)
    eid = "|".join(eid_parts)
    if not await dedup.check_and_record(pool, eid):
        log.debug("webhook.dedup.skip", event_id=eid)
        await obs.record_event("dedup.hit", issue_id=body.issueId, extras={"event_id": eid})
        return {"action": "skip", "reason": "duplicate event", "event_id": eid}

    # ─── 2. Resolve tags（session events 可能没带，从 BKD 拉）──────────────
    tags = body.tags or []
    if not tags or body.event == "session.completed":
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            issue = await bkd.get_issue(body.projectId, body.issueId)
            tags = issue.tags
    log.info("webhook.received", evt=body.event, issue_id=body.issueId, tags=tags)
    await obs.record_event(
        "webhook.received",
        issue_id=body.issueId, tags=tags,
        extras={"event_type": body.event, "issue_number": body.issueNumber},
    )

    # ─── 3. derive event ────────────────────────────────────────────────────
    event = router_lib.derive_event(body.event, tags)
    if event is None:
        log.debug("webhook.no_event_mapping", tags=tags, event_type=body.event)
        return {"action": "skip", "reason": "no event mapping"}

    # ─── 4. resolve req_id ─────────────────────────────────────────────────
    req_id = router_lib.extract_req_id(tags, body.issueNumber)
    if req_id is None:
        log.warning("webhook.no_req_id", tags=tags)
        return {"action": "skip", "reason": "no req_id resolvable"}

    # ─── 5. fetch / init REQ state ─────────────────────────────────────────
    row = await req_state.get(pool, req_id)
    if row is None:
        # 第一次见此 REQ — intent.analyze 才合法 init
        if event != Event.INTENT_ANALYZE:
            log.warning("webhook.req_not_init", req_id=req_id, evt=event.value)
            return {"action": "skip", "reason": "REQ not initialized"}
        await req_state.insert_init(
            pool, req_id, body.projectId,
            context={"intent_issue_id": body.issueId},
        )
        row = await req_state.get(pool, req_id)
        if row is None:
            return {"action": "error", "reason": "init failed"}
    cur_state = row.state
    ctx = row.context

    # ─── 6. 推进状态机（engine 内部循环 emit）─────────────────────────────
    return await engine.step(
        pool,
        body=body,
        req_id=req_id,
        project_id=body.projectId,
        tags=tags,
        cur_state=cur_state,
        ctx=ctx,
        event=event,
    )
