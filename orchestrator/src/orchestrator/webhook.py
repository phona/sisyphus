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
from .state import Event, ReqState
from .store import db, dedup, req_state, verifier_decisions

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


async def _push_upstream_status(project_id: str, issue_id: str, status_id: str) -> None:
    """把刚收到 session.completed 的 BKD issue 状态推到目标 statusId。

    statusId 取值：
    - "done" —— 默认收尾，issue 进 BKD 看板"完成"列
    - "review" —— verifier 判 escalate 时用，issue 进"待审查"列让用户能定位 follow-up
      （resume 路径：用户在该 issue chat 续聊 → BKD wake agent → 新 decision → 主链继续）

    幂等。失败只记 warning，不阻塞状态机。
    """
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            await bkd.update_issue(
                project_id=project_id, issue_id=issue_id, status_id=status_id,
            )
    except Exception as e:
        log.warning("webhook.upstream_status_failed",
                    issue_id=issue_id, status_id=status_id, error=str(e))


@api.get("/bkd-events")
async def webhook_probe() -> dict:
    """GET 探活，给 BKD / 健康巡检用。无需 auth。"""
    return {"status": "ok", "endpoint": "bkd-events", "method": "POST", "auth": "Bearer"}


@api.post("/bkd-events")
async def webhook(request: Request) -> JSONResponse:
    # 顺序很关键：先 auth → 再 body 校验。否则 BKD 注册时无 auth 的探测会拿到 422
    # 误以为格式错而拒绝注册。BKD 注册 webhook 时填的 secret 字段会被 BKD 自动
    # 包成 `Authorization: Bearer <secret>` header 发过来。
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
    # 不要带 timestamp —— BKD 重发时 timestamp 通常变（实测 REQ-final7 同 issue 的
    # session.completed 间隔 10min 重发了一次，timestamp 不同绕过原 dedup → 触发
    # 已 superseded 的 verifier decision，把已推进的 REQ 反向 escalate）。
    # 用 (issueId, event_type, executionId) 作 key —— 同一 BKD execution 只能处理一次
    # session.completed / session.failed；issue.updated 没 executionId 用 timestamp 兜底
    # （issue.updated 不会触发 verifier 决策，timestamp 重发危害有限）
    if body.event in ("session.completed", "session.failed") and body.executionId:
        eid = f"{body.issueId}|{body.event}|{body.executionId}"
    else:
        # issue.updated 等：用 timestamp + issueId + event 兜底
        eid_parts = [body.timestamp or "", body.issueId, body.event]
        if body.executionId:
            eid_parts.append(body.executionId)
        eid = "|".join(eid_parts)
    _dedup_status = await dedup.check_and_record(pool, eid)
    if _dedup_status == "skip":
        log.debug("webhook.dedup.skip", event_id=eid, processed=True)
        await obs.record_event("dedup.hit", issue_id=body.issueId, extras={"event_id": eid})
        return {"action": "skip", "reason": "duplicate event already processed", "event_id": eid}
    if _dedup_status == "retry":
        log.warning("webhook.dedup.retry", event_id=eid,
                    reason="previous attempt crashed mid-flight")

    # ─── 2. Resolve tags（session events 可能没带，从 BKD 拉）──────────────
    tags = body.tags or []
    if not tags or body.event == "session.completed":
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            issue = await bkd.get_issue(body.projectId, body.issueId)
            tags = issue.tags
    log.info("webhook.received", evt=body.event, issue_id=body.issueId, tags=tags)

    # ─── 2.5 早期 noise filter ─────────────────────────────────────────────
    # BKD 推整 project 所有 session.completed，包括跟当前 REQ 无关的旧 issue。
    # 没 REQ-N tag 也不是 intent.analyze 入口的，直接 skip 不浪费 derive/CAS。
    if (
        body.event == "session.completed"
        and not router_lib.extract_req_id(tags)
    ):
        log.debug("webhook.skip_no_req_tag", issue_id=body.issueId, tags=tags)
        await dedup.mark_processed(pool, eid)
        return {"action": "skip", "reason": "session event without REQ tag"}
    await obs.record_event(
        "webhook.received",
        issue_id=body.issueId, tags=tags,
        extras={"event_type": body.event, "issue_number": body.issueNumber},
    )

    # ─── 3. derive event ────────────────────────────────────────────────────
    event = router_lib.derive_event(body.event, tags)

    # M14b：verifier-agent session.completed → 解 decision JSON（tag 或 description）
    # router.derive_event 对 `verifier` tag 主动返 None，交给这里 full parse。
    decision_payload: dict | None = None
    if (
        event is None
        and body.event == "session.completed"
        and "verifier" in set(tags)
    ):
        decision_source = None
        try:
            async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
                # BKD ≥0.0.65 的 issue 对象没 `description` 字段 —— verifier 的
                # decision JSON 写在 session 最后一条 assistant-message 里，
                # 从 /logs API 读。REST 客户端有 get_last_assistant_message；
                # MCP 客户端（老路）fallback 到 issue.description（依然是 None）。
                if hasattr(bkd, "get_last_assistant_message"):
                    decision_source = await bkd.get_last_assistant_message(
                        body.projectId, body.issueId,
                    )
                else:
                    issue = await bkd.get_issue(body.projectId, body.issueId)
                    decision_source = issue.description
        except Exception as e:
            log.warning("webhook.verifier.fetch_decision_failed",
                        issue_id=body.issueId, error=str(e))
        event, decision_payload, why = router_lib.derive_verifier_event(decision_source, tags)
        log.info("webhook.verifier.decision",
                 issue_id=body.issueId, verifier_event=event.value,
                 decision=decision_payload, reason=why)

    # INTAKE_PASS：从 intake-agent 最后一条 message 解 finalized intent JSON
    # 解不到 → 降级为 INTAKE_FAIL 触发 escalate
    intake_finalized_intent: dict | None = None
    if event == Event.INTAKE_PASS:
        intake_text = None
        try:
            async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
                if hasattr(bkd, "get_last_assistant_message"):
                    intake_text = await bkd.get_last_assistant_message(
                        body.projectId, body.issueId,
                    )
        except Exception as e:
            log.warning("webhook.intake.fetch_finalized_intent_failed",
                        issue_id=body.issueId, error=str(e))
        intake_finalized_intent = router_lib.extract_intake_finalized_intent(intake_text)
        if intake_finalized_intent is None:
            log.warning("webhook.intake.no_finalized_intent",
                        issue_id=body.issueId)
            event = Event.INTAKE_FAIL  # 降级：没有有效 finalized intent → escalate

    if event is None:
        log.debug("webhook.no_event_mapping", tags=tags, event_type=body.event)
        await dedup.mark_processed(pool, eid)
        return {"action": "skip", "reason": "no event mapping"}

    # ─── 3.5 把上游 BKD issue 推目标 statusId（webhook 已识别为有效完工信号）──────
    # 默认 "done"。**verifier 判 escalate 例外** → "review"，让 BKD 看板"待审查"列只剩
    # 用户可 follow-up 续作业的 issue（resume 路径）。其他 (analyze/challenger/fixer/checker
    # 完成) 全推 done，UI 干净。session.failed 不推（保留人工排查）。
    if body.event == "session.completed":
        is_verifier_escalate = (
            "verifier" in (tags or [])
            and event == Event.VERIFY_ESCALATE
        )
        await _push_upstream_status(
            body.projectId, body.issueId,
            "review" if is_verifier_escalate else "done",
        )

    # ─── 4. resolve req_id ─────────────────────────────────────────────────
    req_id = router_lib.extract_req_id(tags, body.issueNumber)
    if req_id is None:
        log.warning("webhook.no_req_id", tags=tags)
        await dedup.mark_processed(pool, eid)
        return {"action": "skip", "reason": "no req_id resolvable"}

    # ─── 5. fetch / init REQ state（支持任意 state init via init:STATE tag）────────────────
    row = await req_state.get(pool, req_id)
    init_state = None  # 默认 None，insert_init 会用 ReqState.INIT
    if row is None:
        # 支持通过 init:STATE tag 在任意状态初始化（中流注入其他工作流）
        for tag in tags:
            if tag.startswith("init:"):
                state_str = tag[5:].lower()
                try:
                    init_state = ReqState(state_str)
                    log.info("webhook.init_custom_state", req_id=req_id, state=state_str)
                except ValueError:
                    log.warning("webhook.init_invalid_state", req_id=req_id, state=state_str)
                break

        # 初始化 REQ（默认 INIT 或指定的自定义状态）
        await req_state.insert_init(
            pool, req_id, body.projectId,
            context={
                "intent_issue_id": body.issueId,
                "intent_title": (body.title or "").strip(),
            },
            state=init_state,
        )
        row = await req_state.get(pool, req_id)
        if row is None:
            return {"action": "error", "reason": "init failed"}
    cur_state = row.state
    ctx = row.context

    # ─── 5.6 verifier decision payload 落 ctx（start_fixer 等 action 读）──
    if decision_payload is not None:
        patch = {
            "verifier_fixer": decision_payload.get("fixer"),
            "verifier_scope": decision_payload.get("scope"),
            "verifier_reason": decision_payload.get("reason"),
            "verifier_confidence": decision_payload.get("confidence"),
        }
        await req_state.update_context(pool, req_id, patch)
        ctx = {**ctx, **patch}

        # M14e：落 verifier_decisions（best-effort，失败只 log）
        try:
            await verifier_decisions.insert_decision(
                pool, req_id,
                stage=ctx.get("verifier_stage") or "unknown",
                trigger=ctx.get("verifier_trigger") or "unknown",
                action=decision_payload.get("action"),
                fixer=decision_payload.get("fixer"),
                scope=decision_payload.get("scope"),
                reason=decision_payload.get("reason"),
                confidence=decision_payload.get("confidence"),
            )
        except Exception as e:
            log.warning("webhook.verifier_decisions.write_failed",
                        req_id=req_id, error=str(e))

    # ─── 5.7 intake finalized intent 落 ctx（start_analyze_with_finalized_intent 读）──
    if intake_finalized_intent is not None:
        patch = {
            "intake_finalized_intent": intake_finalized_intent,
            "intake_issue_id": body.issueId,
        }
        await req_state.update_context(pool, req_id, patch)
        ctx = {**ctx, **patch}

    # ─── 6. 推进状态机（engine 内部循环 emit）─────────────────────────────
    result = await engine.step(
        pool,
        body=body,
        req_id=req_id,
        project_id=body.projectId,
        tags=tags,
        cur_state=cur_state,
        ctx=ctx,
        event=event,
    )
    # handler 跑完，标 processed_at。engine.step 抛异常时不到这里，BKD 重发会走 retry 路径。
    await dedup.mark_processed(pool, eid)
    return result
