"""escalate: 终态兜底 + auto-resume on transient failure。

行为：
1. transient 失败（session.failed / watchdog-stuck / runner-pod-not-ready）+ retry_count < 2:
   → BKD follow-up 当前 issue "continue, you were interrupted"
   → ctx.auto_retry_count++
   → state 不动（等 BKD 新 session.completed 走原 transition）
2. 否则（retry 用完 / verifier 主动判 escalate）:
   → 在 intent issue 上加 `escalated` + `reason:<细分>` tag
   → 落 ctx 标记 escalated_reason
   → 解析 incident-target repos（involved_repos 5 层 fallback，layer 5 是
     legacy settings.gh_incident_repo）；对每个 repo 独立 POST GH issue，
     idempotent on ctx.gh_incident_urls；至少一条成功 → 加 `github-incident` BKD tag
   → state 进 ESCALATED

不在 BKD 开新 issue（避免污染列表）；不 cancel 当前 issue（让人工有现场）。
GH issue 是给人看的事故面板，不在 BKD 里复制；REST 失败不阻塞 escalate。
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog

from .. import gh_incident, k8s_runner
from ..bkd import BKDClient
from ..config import settings
from ..state import Event, ReqState
from ..store import db, req_state
from . import register
from ._clone import resolve_repos

log = structlog.get_logger(__name__)

_MAX_AUTO_RETRY = 2

# 算 transient（值得 auto-resume）的 reason / event
_TRANSIENT_REASONS = {
    "session-failed",
    "watchdog-stuck",
    "runner-pod-not-ready",
    "archive-failed",  # done-archive 阶段失败（state==ARCHIVING）
    "session-failed-after-2-retries",  # 兜底防自循环
    "archive-failed-after-2-retries",  # 兜底防自循环（archive 路径）
}

# Hard reasons：明确叫人停 + 不允许 auto-resume 绕过。
# 即使 body.event 是 canonical 信号（watchdog.stuck / session.failed），ctx 里只要
# 写了这些 reason，escalate 就用它（避免 watchdog 把 fixer-round-cap 这类硬终止
# 二次包装成 watchdog-stuck → 被 _is_transient 判 transient → 继续 auto-resume → 再
# 多起一轮 fixer，回到死循环）。
_HARD_REASONS = {"fixer-round-cap"}


def _is_transient(body_event: str | None, reason: str) -> bool:
    """判断是不是 transient 失败：值得 auto-resume continue 一次"""
    if reason in _HARD_REASONS:
        return False  # 硬停，绝不 auto-resume
    if reason == "verifier-decision-escalate":
        return False  # verifier 主观判，不重试
    if body_event == "session.failed":
        return True
    if body_event == "watchdog.stuck":
        return True  # watchdog 兜底永远值得续一次（BKD 漏发 webhook / process 卡住等）
    if body_event == "archive.failed":
        return True  # watchdog 在 ARCHIVING 阶段贴的 archive 专属信号
    if reason in _TRANSIENT_REASONS:
        return True
    if reason.startswith("action-error:"):
        # engine _emit_escalate 注的：action handler 抛异常多半是基础设施 flaky
        # （pod 没起、K3s 慢、BKD 临时 5xx）。续一次合理；真 bug 第二次还会同样异常
        # 走 retry 用完 → 真 escalate。
        return True
    return False


# canonical 失败信号：body.event 取这几个值时，reason 直接由 body.event slug 化得到
# （避免被前轮 ctx.escalated_reason 毒化）。
# - session.failed: BKD 真发的 webhook
# - watchdog.stuck: watchdog 兜底
# - archive.failed: watchdog 在 ARCHIVING state 贴的细分信号（让 reason="archive-failed"
#   能在 dashboard 上跟通用 watchdog-stuck 区分）
_CANONICAL_SIGNALS = {"session.failed", "watchdog.stuck", "archive.failed"}

# 走 SESSION_FAILED transition 的 body.event 都需要在 escalate 末尾手动 CAS 推到
# ESCALATED + 清 runner（transition 是 self-loop，engine 不自动清）。
# watchdog.intake_no_result_tag：watchdog 检测到 intake 完成但忘 PATCH result tag，
#   这类终止信号必须走 cleanup（session 已 done，绕开 _CANONICAL_SIGNALS 让
#   escalate.py 优先采用 ctx.escalated_reason="intake-no-result-tag"）。
_SESSION_END_SIGNALS = {
    "session.failed",
    "watchdog.stuck",
    "watchdog.intake_no_result_tag",
    "archive.failed",
}


def _resolve_incident_repos(ctx: dict | None, tags) -> list[str]:
    """Layered fallback for "where do incidents land for this REQ?"

    Layers 1-4 mirror clone resolution (intake_finalized_intent / ctx.involved_repos /
    `repo:` tags / settings.default_involved_repos). Layer 5 is settings.gh_incident_repo
    — the legacy single-inbox knob, only consulted when 1-4 are all empty (intake-stage
    failures pre-clone, "central triage queue" deployments).
    """
    repos, _src = resolve_repos(
        ctx, tags=tags, default_repos=settings.default_involved_repos,
    )
    if repos:
        return repos
    fallback = (settings.gh_incident_repo or "").strip()
    return [fallback] if fallback else []


@register("escalate", idempotent=True)
async def escalate(*, body, req_id, tags, ctx):
    proj = body.projectId
    intent_issue_id = (ctx or {}).get("intent_issue_id") or body.issueId
    failed_issue_id = body.issueId  # 这次崩的具体 BKD issue
    # reason 优先级：
    #   1. ctx hard reason（fixer-round-cap 等）—— 即使 body.event 是 canonical
    #      信号也不能被覆盖，否则 watchdog.stuck 会把 hard 终止误归为 transient
    #   2. body.event 是 canonical 失败信号（session.failed / watchdog.stuck）
    #      → 用 body.event（最新一手信号；避免被前轮 ctx.escalated_reason 毒化）
    #   3. ctx.escalated_reason 已被 caller 细分（engine action-error 等）
    #   4. fallback：body.event 转 slug
    ctx_reason = (ctx or {}).get("escalated_reason")
    if ctx_reason in _HARD_REASONS:
        reason = ctx_reason
    elif body.event in _CANONICAL_SIGNALS:
        reason = body.event.replace(".", "-")[:40]
    else:
        reason = ctx_reason or (
            (body.event or "unknown").replace(".", "-")[:40]
        )
    # 二次 override：BKD 真发的 session.failed webhook 也能识别 archive 阶段
    # （body.issueId == ctx.archive_issue_id 说明是 done-archive agent 崩溃）。
    # watchdog 路径已经直接贴 body.event="archive.failed"，命中上面的 canonical 分支
    # 自然得到 "archive-failed"；这里专门补 BKD webhook 路径。
    archive_issue_id = (ctx or {}).get("archive_issue_id")
    if (
        body.event == "session.failed"
        and archive_issue_id
        and failed_issue_id == archive_issue_id
    ):
        reason = "archive-failed"
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
        if reason == "archive-failed":
            final_reason = "archive-failed-after-2-retries"
        else:
            final_reason = "session-failed-after-2-retries"

    pool = db.get_pool()

    # ─── GH 事故 issue（per-involved-repo loop, idempotent on ctx.gh_incident_urls） ─
    # 仅在 real-escalate 路径开 issue（auto-resume 不开，会刷屏）。Layer 1-4 是
    # involved_repos（跟 clone helper 对齐：哪几个仓被 clone，事故就在那几个仓里
    # 落 issue）；Layer 5 是 settings.gh_incident_repo（legacy single-inbox fallback，
    # intake 阶段失败 pre-clone / 集中三角部署）。
    existing_urls = dict((ctx or {}).get("gh_incident_urls") or {})
    incident_repos = _resolve_incident_repos(ctx, tags)
    new_urls: dict[str, str] = {}
    if incident_repos:
        # 取当前 state 给 issue body（best-effort，None 也能继续）
        try:
            row = await req_state.get(pool, req_id)
            state_str = row.state.value if row else None
        except Exception:
            state_str = None
        for incident_repo in incident_repos:
            if incident_repo in existing_urls:
                continue  # idempotent: 此 repo 已开过 issue
            url = await gh_incident.open_incident(
                repo=incident_repo,
                req_id=req_id,
                reason=final_reason,
                retry_count=retry_count,
                intent_issue_id=intent_issue_id,
                failed_issue_id=failed_issue_id,
                project_id=proj,
                state=state_str,
            )
            if url:
                new_urls[incident_repo] = url

    merged_urls = {**existing_urls, **new_urls}

    add_tags = ["escalated", f"reason:{final_reason}"]
    if merged_urls:
        # snapshot._STAGE_FROM_TAGS 已包含 'github-incident'，让 Metabase 看板自然识别
        add_tags.append("github-incident")

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        try:
            await bkd.merge_tags_and_update(
                proj, intent_issue_id,
                add=add_tags,
            )
        except Exception as e:
            log.warning("escalate.tag_failed", req_id=req_id, error=str(e))

    ctx_patch = {
        "escalated_reason": final_reason,
        "escalated_source_issue_id": failed_issue_id,
        "escalated_retry_count": retry_count,
    }
    if new_urls:
        # 全量替换 dict（merge of existing_urls + new_urls 已在 merged_urls）
        ctx_patch["gh_incident_urls"] = merged_urls
        # legacy single-URL field：保留首次成功 POST 的 URL，让 admin view /
        # Metabase 旧 query 继续工作。优先 existing（保持旧值），否则取新 URL 第一条。
        legacy_url = (ctx or {}).get("gh_incident_url") or next(iter(new_urls.values()))
        ctx_patch["gh_incident_url"] = legacy_url
        ctx_patch["gh_incident_opened_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    await req_state.update_context(pool, req_id, ctx_patch)

    # SESSION_FAILED 类路径下 transition 是 self-loop（state 没动），需手动 CAS 推到
    # ESCALATED 并清 runner。
    # 触发源：BKD 真发的 session.failed webhook，或 watchdog 内部 emit Event.SESSION_FAILED
    # （body.event="watchdog.stuck" 通用 / "archive.failed" 在 ARCHIVING state 上）。
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
