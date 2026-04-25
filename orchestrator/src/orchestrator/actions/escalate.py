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
    if reason == "verifier-decision":
        return False  # verifier 主观判，不重试
    if body_event == "session.failed":
        return True
    if body_event == "watchdog.stuck":
        return True  # watchdog 兜底永远值得续一次（BKD 漏发 webhook / process 卡住等）
    if reason in _TRANSIENT_REASONS:
        return True
    if reason.startswith("action-error:"):
        # engine _emit_escalate 注的：action handler 抛异常多半是基础设施 flaky
        # （pod 没起、K3s 慢、BKD 临时 5xx）。续一次合理；真 bug 第二次还会同样异常
        # 走 retry 用完 → 真 escalate。
        return True
    return False


_CANONICAL_SIGNALS = {"session.failed", "watchdog.stuck"}

# 走 SESSION_FAILED transition 的 body.event 都需要在 escalate 末尾手动 CAS 推到
# ESCALATED + 清 runner（transition 是 self-loop，engine 不自动清）。
# watchdog.intake_no_result_tag：watchdog 检测到 intake 完成但忘 PATCH result tag，
#   这类终止信号必须走 cleanup（session 已 done，绕开 _CANONICAL_SIGNALS 让
#   escalate.py 优先采用 ctx.escalated_reason="intake-no-result-tag"）。
_SESSION_END_SIGNALS = {
    "session.failed",
    "watchdog.stuck",
    "watchdog.intake_no_result_tag",
}


@register("escalate", idempotent=True)
async def escalate(*, body, req_id, tags, ctx):
    proj = body.projectId
    intent_issue_id = (ctx or {}).get("intent_issue_id") or body.issueId
    failed_issue_id = body.issueId  # 这次崩的具体 BKD issue
    # reason 优先级：
    #   1. body.event 是 canonical 失败信号（session.failed / watchdog.stuck）
    #      → 用 body.event（最新一手信号；避免被前轮 ctx.escalated_reason 毒化）
    #   2. ctx.escalated_reason 已被 caller 细分（engine action-error 等）
    #   3. fallback：body.event 转 slug
    if body.event in _CANONICAL_SIGNALS:
        reason = body.event.replace(".", "-")[:40]
    else:
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
    is_session_failed_path = body.event in _SESSION_END_SIGNALS
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
