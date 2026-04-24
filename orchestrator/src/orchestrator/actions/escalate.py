"""escalate: 终态兜底 + auto-resume on transient failure。

行为：
1. transient 失败（session.failed / watchdog-stuck / runner-pod-not-ready）+ retry_count < 2:
   → BKD follow-up 当前 issue "continue, you were interrupted"
   → ctx.auto_retry_count++
   → state 不动（等 BKD 新 session.completed 走原 transition）
2. 否则（retry 用完 / verifier 主动判 escalate）:
   → 在 intent issue 上加 `escalated` + `reason:<细分>` tag
   → 落 ctx 标记 escalated_reason
   → state 进 ESCALATED

不开新 issue（避免污染列表）；不 cancel 当前 issue（让人工有现场）。
"""
from __future__ import annotations

import structlog

from .. import k8s_runner
from ..bkd import BKDClient
from ..config import settings
from ..state import Event, ReqState
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)

_MAX_AUTO_RETRY = 2

# 算 transient（值得 auto-resume）的 reason / event
_TRANSIENT_REASONS = {
    "session-failed",
    "watchdog-stuck",
    "runner-pod-not-ready",
    "session-failed-after-2-retries",  # 兜底防自循环
}


def _is_transient(body_event: str | None, reason: str) -> bool:
    """判断是不是 transient 失败：值得 auto-resume continue 一次"""
    if reason == "verifier-decision-escalate":
        return False  # verifier 主观判，不重试
    if body_event == "session.failed":
        return True
    if reason in _TRANSIENT_REASONS:
        return True
    return False


@register("escalate", idempotent=True)
async def escalate(*, body, req_id, tags, ctx):
    proj = body.projectId
    intent_issue_id = (ctx or {}).get("intent_issue_id") or body.issueId
    failed_issue_id = body.issueId  # 这次崩的具体 BKD issue
    # ctx.escalated_reason 优先（caller 已细分），fallback 到 event 名
    reason = (ctx or {}).get("escalated_reason") or (
        (body.event or "unknown").replace(".", "-")[:40]
    )
    retry_count = (ctx or {}).get("auto_retry_count", 0)

    # ─── 1. transient + retry < 2 → auto-resume ────────────────────────────
    if _is_transient(body.event, reason) and retry_count < _MAX_AUTO_RETRY:
        try:
            async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
                await bkd.follow_up_issue(
                    proj, failed_issue_id,
                    f"⚠️ Session was interrupted (reason: {reason}). "
                    f"Auto-resume attempt {retry_count + 1}/{_MAX_AUTO_RETRY}. "
                    f"Please continue from where you left off based on the chat history above.",
                )
        except Exception as e:
            # follow-up 失败（BKD 自己挂等）→ fall through to 真 escalate
            log.warning("escalate.auto_resume.followup_failed",
                        req_id=req_id, error=str(e))
        else:
            pool = db.get_pool()
            await req_state.update_context(pool, req_id, {
                "auto_retry_count": retry_count + 1,
                "last_retry_reason": reason,
            })
            log.warning("escalate.auto_resume",
                        req_id=req_id,
                        retry=f"{retry_count + 1}/{_MAX_AUTO_RETRY}",
                        reason=reason,
                        failed_issue=failed_issue_id)
            # state 不动 —— 等 BKD wake agent → 新 session.completed → 走主链
            return {"auto_resumed": True, "retry": retry_count + 1, "reason": reason}

    # ─── 2. 真 escalate：retry 用完 / non-transient (verifier escalate / intake-fail / pr-ci-timeout 等) ─
    final_reason = reason
    if retry_count >= _MAX_AUTO_RETRY and _is_transient(body.event, reason):
        final_reason = "session-failed-after-2-retries"

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        try:
            await bkd.merge_tags_and_update(
                proj, intent_issue_id,
                add=["escalated", f"reason:{final_reason}"],
            )
        except Exception as e:
            log.warning("escalate.tag_failed", req_id=req_id, error=str(e))

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "escalated_reason": final_reason,
        "escalated_source_issue_id": failed_issue_id,
        "escalated_retry_count": retry_count,
    })

    # SESSION_FAILED 类路径下 transition 是 self-loop（state 没动），需手动 CAS 推到
    # ESCALATED 并清 runner。
    # 触发源：BKD 真发的 session.failed webhook，或 watchdog 内部 emit Event.SESSION_FAILED
    # （body.event="watchdog.stuck"）。
    # 其他事件路径（如 INTAKE_FAIL / PR_CI_TIMEOUT / VERIFY_ESCALATE）的 transition
    # 已在 state.py 写死 next_state=ESCALATED，engine 已经做过 CAS + cleanup，这里跳过。
    is_session_failed_path = body.event in ("session.failed", "watchdog.stuck")
    if is_session_failed_path:
        row = await req_state.get(pool, req_id)
        if row and row.state != ReqState.ESCALATED:
            advanced = await req_state.cas_transition(
                pool, req_id, row.state, ReqState.ESCALATED,
                Event.SESSION_FAILED, "escalate",
            )
            if advanced:
                # 手动清 runner（engine 没自动清，因为 transition 是 self-loop 看不出 terminal）
                try:
                    rc = k8s_runner.get_controller()
                    await rc.cleanup_runner(req_id, retain_pvc=True)
                    log.info("escalate.runner_cleaned", req_id=req_id)
                except Exception as e:
                    log.warning("escalate.runner_cleanup_failed",
                                req_id=req_id, error=str(e))

    log.warning("escalate.final",
                req_id=req_id, reason=final_reason,
                retry_count=retry_count, issue_id=intent_issue_id)
    return {"escalated": True, "reason": final_reason, "retry_count": retry_count}
