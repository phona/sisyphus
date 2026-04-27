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

from . import engine, links
from . import observability as obs
from . import router as router_lib
from .bkd import BKDClient
from .config import settings
from .state import Event, ReqState
from .store import db, dedup, req_state, stage_runs, verifier_decisions

log = structlog.get_logger(__name__)
api = APIRouter()


_BEARER = "bearer "


# BAFL Case 2: state-aware acceptance routing helpers ─────────────────────
# Tag names the user adds to the BKD intent issue while it sits in
# PENDING_USER_ACCEPT to signal their decision.
_BAFL_TAG_TO_EVENT: dict[str, Event] = {
    "acceptance:approve": Event.ACCEPT_USER_APPROVED,
    "acceptance:request-changes": Event.ACCEPT_USER_REQUEST_CHANGES,
    "acceptance:reject": Event.ACCEPT_USER_REJECTED,
}


def _derive_pending_user_accept_event(
    tags: list[str] | None,
    changes: dict | None,
) -> Event | None:
    """Pick an `ACCEPT_USER_*` event from intent-issue tags / statusId change.

    Precedence:
      1. acceptance:approve            → ACCEPT_USER_APPROVED
      2. acceptance:request-changes    → ACCEPT_USER_REQUEST_CHANGES
      3. acceptance:reject             → ACCEPT_USER_REJECTED
      4. body.changes.statusId == "done" without any acceptance:* tag
         → ACCEPT_USER_REJECTED (user closed the issue without explicit tag)

    Returns None when none of the conditions match — caller falls through
    to the regular `derive_event` path.
    """
    tagset = set(tags or [])
    for tag, evt in _BAFL_TAG_TO_EVENT.items():
        if tag in tagset:
            return evt
    if changes and changes.get("statusId") == "done":
        return Event.ACCEPT_USER_REJECTED
    return None


async def _fetch_latest_user_message(
    project_id: str, issue_id: str,
) -> str | None:
    """Best-effort: pull the most recent **user-authored** chat entry on a
    BKD issue. Used to populate `ctx.verifier_reason` when routing
    `ACCEPT_USER_REQUEST_CHANGES`.

    Returns None if BKD is unreachable / the logs API has no user entries.
    """
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            # The BKD logs endpoint mixes user + assistant messages; we
            # walk it ourselves rather than relying on
            # `get_last_assistant_message` (which filters the wrong way).
            # `get_last_user_message` is added on the REST client below
            # via duck-typing with hasattr to avoid hard-failing on
            # MCP transport.
            if hasattr(bkd, "get_last_user_message"):
                return await bkd.get_last_user_message(project_id, issue_id)
            # MCP transport / older REST client: best fallback is the
            # last assistant message (carries no user words but at least
            # surfaces the most recent context).
            if hasattr(bkd, "get_last_assistant_message"):
                return await bkd.get_last_assistant_message(project_id, issue_id)
    except Exception as e:
        log.warning("webhook.bafl.fetch_user_message_failed",
                    issue_id=issue_id, error=str(e))
    return None


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
    # session.failed 也走 fetch 路径：除了拿 tags，还顺手抓 externalSessionId
    # 写进 stage_runs，让 dashboard 能从崩掉的 stage_run 直接跳到对应 BKD chat
    # 排查 agent 行为。getattr 防御式读 —— 老的 BKD payload / 测试 stub 可能没该字段。
    tags = body.tags or []
    bkd_session_id: str | None = None
    if not tags or body.event in ("session.completed", "session.failed"):
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            issue = await bkd.get_issue(body.projectId, body.issueId)
            tags = issue.tags
            bkd_session_id = getattr(issue, "external_session_id", None)
    log.info("webhook.received", evt=body.event, issue_id=body.issueId, tags=tags)

    # ─── 2.5 早期 noise filter ─────────────────────────────────────────────
    # BKD 推整 project 所有 webhook 事件，包括跟当前 REQ 无关的旧 issue / 别人手动改的卡。
    #   session.completed: 没 REQ tag → 别的工作流的孤儿 session，skip
    #   issue.updated:     没 REQ tag 也没 intent 入口 tag → 跟任何 sisyphus REQ 都没关系
    #                      （唯一合法触发：REQ workflow 内 issue 的 tag/result 变化，或
    #                       用户在 intent issue 上打 intent:intake/analyze 触发新 REQ）
    # 早 skip 避免后续 obs.record_event / derive_event / engine.step 白跑 + 污染 event log。
    has_req_tag = bool(router_lib.extract_req_id(tags))
    if body.event == "session.completed" and not has_req_tag:
        log.debug("webhook.skip_no_req_tag", issue_id=body.issueId, tags=tags)
        await dedup.mark_processed(pool, eid)
        return {"action": "skip", "reason": "session event without REQ tag"}
    if (
        body.event == "issue.updated"
        and not has_req_tag
        and "intent:intake" not in tags
        and "intent:analyze" not in tags
    ):
        log.debug("webhook.skip_no_req_or_intent_tag",
                  issue_id=body.issueId, tags=tags)
        await dedup.mark_processed(pool, eid)
        return {"action": "skip", "reason": "issue.updated without REQ or intent tag"}
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

    # INTAKE_PASS：扫所有 assistant-messages 找 finalized intent JSON
    # （不像 verifier 严要求 "JSON 必须放最后一条"，intake-agent 常先贴 JSON 再发短消息）
    # 解不到 → 降级为 INTAKE_FAIL 触发 escalate
    intake_finalized_intent: dict | None = None
    if event == Event.INTAKE_PASS:
        intake_text = None
        try:
            async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
                if hasattr(bkd, "get_all_assistant_messages_concat"):
                    intake_text = await bkd.get_all_assistant_messages_concat(
                        body.projectId, body.issueId,
                    )
                elif hasattr(bkd, "get_last_assistant_message"):
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

    # ─── 3.5 不在这里早 skip event=None：BAFL Case 2 的 acceptance:* / statusId
    # 触发是 state-aware 的（router.derive_event 看不到 cur_state），需要在 fetch
    # row 之后才能 override。下面在 §5.6 之前再做 final none-skip。

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
        init_ctx: dict = {
            "intent_issue_id": body.issueId,
            "intent_title": (body.title or "").strip(),
        }
        # REQ-pr-issue-traceability-1777218612: 把 BKD 前端 URL 一并落库，
        # 让 gh_incident body / Metabase 看板渲染 clickable 链接，不必每次再算。
        bkd_url = links.bkd_issue_url(body.projectId, body.issueId)
        if bkd_url is not None:
            init_ctx["bkd_intent_url"] = bkd_url
        await req_state.insert_init(
            pool, req_id, body.projectId,
            context=init_ctx,
            state=init_state,
        )
        row = await req_state.get(pool, req_id)
        if row is None:
            return {"action": "error", "reason": "init failed"}
    cur_state = row.state
    ctx = row.context

    # ─── 5.5 BAFL Case 2: state-aware acceptance routing on intent issue ────
    # router.derive_event 看不到 cur_state；用户在 BKD intent issue 打 acceptance:*
    # tag / 把 statusId 翻成 done，靠这里识别 + override event。
    if (
        body.event == "issue.updated"
        and cur_state == ReqState.PENDING_USER_ACCEPT
        and body.issueId == (ctx or {}).get("intent_issue_id", body.issueId)
    ):
        bafl_event = _derive_pending_user_accept_event(tags, body.changes)
        if bafl_event is not None:
            log.info("webhook.pending_user_accept.routed",
                     req_id=req_id, evt=bafl_event.value, tags=tags)
            event = bafl_event
            # ACCEPT_USER_REQUEST_CHANGES：fetch latest user message from the
            # intent issue chat → pre-populate ctx.verifier_* so the existing
            # `start_fixer` action runs unchanged.
            if bafl_event == Event.ACCEPT_USER_REQUEST_CHANGES:
                user_feedback = await _fetch_latest_user_message(
                    body.projectId, body.issueId,
                )
                patch = {
                    "verifier_stage": "accept",
                    "verifier_fixer": "dev",
                    "verifier_reason": user_feedback or "",
                }
                await req_state.update_context(pool, req_id, patch)
                ctx = {**ctx, **patch}
            elif bafl_event == Event.ACCEPT_USER_REJECTED:
                # Hard reason — escalate.py 不会 auto-resume（不在 _TRANSIENT_REASONS）
                await req_state.update_context(pool, req_id, {
                    "escalated_reason": "user-rejected-acceptance",
                })
                ctx = {**ctx, "escalated_reason": "user-rejected-acceptance"}

    # ─── 5.55 final none-skip（state-aware override 之后）────────────────────
    if event is None:
        log.debug("webhook.no_event_mapping", tags=tags, event_type=body.event)
        await dedup.mark_processed(pool, eid)
        return {"action": "skip", "reason": "no event mapping"}

    # ─── 5.56 push BKD upstream status（已识别有效完工信号）──────────────────
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
            # 软验证 audit 字段（fixer-audit REQ）
            audit_raw = decision_payload.get("audit") if isinstance(decision_payload.get("audit"), dict) else None
            audit_warn = router_lib.validate_audit_soft(audit_raw)
            if audit_warn:
                log.warning("webhook.verifier_decisions.audit_invalid",
                            req_id=req_id, reason=audit_warn)
                audit_raw = None
            await verifier_decisions.insert_decision(
                pool, req_id,
                stage=ctx.get("verifier_stage") or "unknown",
                trigger=ctx.get("verifier_trigger") or "unknown",
                action=decision_payload.get("action"),
                fixer=decision_payload.get("fixer"),
                scope=decision_payload.get("scope"),
                reason=decision_payload.get("reason"),
                confidence=decision_payload.get("confidence"),
                audit=audit_raw,
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

    # ─── 5.8 VERIFY_ESCALATE → 预置 escalated_reason ────────────────────────
    # escalate action 从 ctx.escalated_reason 读 reason；没设则 fallback 到
    # body.event.replace(".", "-") = "session-completed"，语义不明。
    if event == Event.VERIFY_ESCALATE:
        await req_state.update_context(pool, req_id, {"escalated_reason": "verifier-decision"})
        ctx = {**ctx, "escalated_reason": "verifier-decision"}

    # ─── 5.9 stamp BKD session token onto 当前开着的 stage_run ───────────────
    # 必须放在 engine.step 之前：engine 在 transition 时会 close_latest_stage_run，
    # 之后这条 row 就不再开着了，stamp 找不到目标。close 只 UPDATE
    # ended_at/outcome/fail_reason/duration_sec，不动 bkd_session_id，所以
    # stamp 在前 close 在后能保留 token。机械 stage（spec_lint 等）不在
    # AGENT_STAGES 里，跳过；fetch issue 失败 → bkd_session_id 仍是 None，跳过。
    if bkd_session_id:
        cur_stage = engine.STATE_TO_STAGE.get(cur_state)
        if cur_stage in engine.AGENT_STAGES:
            try:
                await stage_runs.stamp_bkd_session_id(
                    pool, req_id, cur_stage, bkd_session_id,
                )
            except Exception as e:
                log.warning("webhook.stamp_bkd_session_id_failed",
                            req_id=req_id, stage=cur_stage, error=str(e))

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
