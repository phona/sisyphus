"""Webhook handler：BKD → 状态机 → action dispatch。

唯一入口 /bkd-events，收所有 BKD webhook（issue.updated / session.completed / session.failed）。
handler 内部按 body.event 字段分流。
内部 decide+CAS+dispatch 走 engine.step（让 action 也能链式 emit 事件）。
"""
from __future__ import annotations

import hmac
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import engine
from . import observability as obs
from . import router as router_lib
from .bkd import BKDClient
from .config import settings
from .state import Event
from .store import db, dedup, req_state

log = structlog.get_logger(__name__)
api = APIRouter()


_BEARER = "bearer "


def _verify_token(authorization: str | None) -> None:
    """共享 token 校验：Authorization: Bearer <token>。常量时间比较防 timing。"""
    expected = settings.webhook_token
    provided = ""
    if authorization and authorization.lower().startswith(_BEARER):
        provided = authorization[len(_BEARER):].strip()
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        log.warning("webhook.auth_failed", has_header=bool(authorization))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid Authorization: Bearer <token>",
            headers={"WWW-Authenticate": 'Bearer realm="sisyphus"'},
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


@api.get("/bkd-events")
async def webhook_probe() -> dict:
    """GET 探活，给 BKD / 健康巡检用。无需 auth。"""
    return {"status": "ok", "endpoint": "bkd-events", "method": "POST", "auth": "Bearer"}


@api.post("/bkd-events")
async def webhook(request: Request) -> JSONResponse:
    # 顺序很关键：先 auth → 再 body 校验。否则 BKD 注册时无 auth 的探测会拿到 422
    # 误以为格式错而拒绝注册。
    _verify_token(request.headers.get("authorization"))
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body") from None
    try:
        body = WebhookBody.model_validate(raw)
    except ValidationError as e:
        return JSONResponse(status_code=422, content={"detail": e.errors()})

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
