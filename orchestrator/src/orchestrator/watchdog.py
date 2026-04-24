"""M8 watchdog 后台任务：兜底卡死的 stage。

背景：dev BKD agent spawn 失败（session=failed 但 0 logs）时 BKD 不发
session.failed webhook，orchestrator 永远卡在 in-flight state 等不来事件。
M4 retry policy 假设"失败事件总会到"被打破 — watchdog 作为独立兜底。

每 N 秒扫一次 req_state，发现某 REQ：
1. state 在 in-flight（非 done / 非 escalated / 非 init）
2. updated_at 距今超过 _WARN_THRESHOLD_SEC（5min）

→ 5min–30min：插 alert(warn) + 在 ctx 标 warned_at_5min（只告一次）
→ 超过 watchdog_stuck_threshold_sec（30min）且 BKD session 不在 running：
  写 artifact_checks 记录 + 通过 engine.step 发 SESSION_FAILED 走 escalate。

不 restart agent（restart 归 M4 retry policy 管），只 escalate。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import structlog

from . import alerts, engine
from .bkd import BKDClient
from .checkers._types import CheckResult
from .config import settings
from .state import Event, ReqState
from .store import artifact_checks, db, req_state

log = structlog.get_logger(__name__)

# 5min warn 阈值（固定，不做配置）
_WARN_THRESHOLD_SEC = 300


# state → ctx 里追踪该 stage 当前 agent issue 的 key
# None 表示没 issue 可查（直接 escalate）
_STATE_ISSUE_KEY: dict[ReqState, str | None] = {
    ReqState.ANALYZING: "intent_issue_id",
    ReqState.SPEC_LINT_RUNNING: None,            # M15: 客观 checker，由 orchestrator 下发不绑 issue
    ReqState.DEV_CROSS_CHECK_RUNNING: None,      # M15: 客观 checker，由 orchestrator 下发不绑 issue
    ReqState.STAGING_TEST_RUNNING: "staging_test_issue_id",
    ReqState.PR_CI_RUNNING: "pr_ci_watch_issue_id",
    ReqState.ACCEPT_RUNNING: "accept_issue_id",
    ReqState.ACCEPT_TEARING_DOWN: "accept_issue_id",
    ReqState.REVIEW_RUNNING: "verifier_issue_id",
    ReqState.FIXER_RUNNING: "fixer_issue_id",
    ReqState.ARCHIVING: "archive_issue_id",
}

# 排除：终态 + 等人态 + 未入链
_SKIP_STATES = {
    ReqState.DONE.value,
    ReqState.ESCALATED.value,
    ReqState.GH_INCIDENT_OPEN.value,
    ReqState.INIT.value,
}


@dataclass
class _SyntheticBody:
    """engine.step / escalate action 对 body 的最小字段依赖。"""
    projectId: str
    issueId: str
    event: str = "watchdog.stuck"


async def _tick() -> dict:
    """单次扫描：warn 5min stuck + escalate 30min stuck。返回 {checked, escalated, warned}。"""
    pool = db.get_pool()
    # 扫所有 stuck > 5min 的（含 5-30min warn 区间 + 30min+ escalate 区间）
    rows = await pool.fetch(
        """
        SELECT req_id, project_id, state, context,
               EXTRACT(EPOCH FROM (NOW() - updated_at))::BIGINT AS stuck_sec
          FROM req_state
         WHERE state <> ALL($1::text[])
           AND updated_at < NOW() - INTERVAL '1 second' * $2
        """,
        list(_SKIP_STATES), _WARN_THRESHOLD_SEC,
    )
    escalated = 0
    warned = 0
    for row in rows:
        outcome = await _check_and_maybe_alert(row)
        if outcome == "escalated":
            escalated += 1
        elif outcome == "warned":
            warned += 1
    return {"checked": len(rows), "escalated": escalated, "warned": warned}


async def _check_and_maybe_alert(row) -> str:
    """检查一条 stuck row。返回 'escalated' / 'warned' / 'skip'。"""
    req_id = row["req_id"]
    project_id = row["project_id"]
    state_str = row["state"]
    ctx_raw = row["context"] or {}
    # asyncpg 返回 JSONB 可能是 dict 或 str
    ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
    stuck_sec = int(row["stuck_sec"])

    try:
        state = ReqState(state_str)
    except ValueError:
        log.warning("watchdog.unknown_state", req_id=req_id, state=state_str)
        return "skip"

    # 5-30min：warn path（BKD session 仍 running 就跳过）
    if stuck_sec < settings.watchdog_stuck_threshold_sec:
        if ctx.get("warned_at_5min"):
            return "skip"  # 已经告过，不重复

        # 简单 check：有 issue_id 才查 BKD（避免无意义 warn）
        issue_key = _STATE_ISSUE_KEY.get(state)
        issue_id = ctx.get(issue_key) if issue_key else None
        if issue_id:
            try:
                async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
                    issue = await bkd.get_issue(project_id, issue_id)
                if issue.session_status == "running":
                    return "skip"
            except Exception as e:
                log.warning("watchdog.bkd_check_warn_failed",
                            req_id=req_id, issue_id=issue_id, error=str(e))

        try:
            await alerts.insert(
                severity="warn",
                req_id=req_id,
                stage=state.value,
                reason="stuck-5min",
                hint=f"REQ has not advanced for {stuck_sec}s",
            )
        except Exception as e:
            log.warning("watchdog.warn_insert_failed", req_id=req_id, error=str(e))

        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {"warned_at_5min": True})
        log.warning("watchdog.warn_5min", req_id=req_id, state=state_str, stuck_sec=stuck_sec)
        return "warned"

    # >=30min：escalate path（原逻辑 + escalated_reason 细分）
    issue_key = _STATE_ISSUE_KEY.get(state)
    issue_id: str | None = None
    if issue_key:
        issue_id = ctx.get(issue_key)

    # 1. 查 BKD session 状态（有 issue_id 才查）
    still_running = False
    if issue_id:
        try:
            async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
                issue = await bkd.get_issue(project_id, issue_id)
            session_status = issue.session_status
            if session_status == "running":
                still_running = True
                log.debug(
                    "watchdog.still_running",
                    req_id=req_id, state=state_str,
                    issue_id=issue_id, stuck_sec=stuck_sec,
                )
        except Exception as e:
            # 查不到（issue 删了 / BKD 挂了）→ 保守按 failed 处理，走 escalate
            log.warning(
                "watchdog.bkd_get_issue_failed",
                req_id=req_id, issue_id=issue_id, error=str(e),
            )

    if still_running:
        return "skip"

    # 2. 写 artifact_checks 记一笔，给 dashboard M7 04-fail-kind-distribution 抓
    pool = db.get_pool()
    check = CheckResult(
        passed=False,
        exit_code=-1,
        cmd=f"watchdog:{state_str}",
        stdout_tail="",
        stderr_tail=f"stuck for {stuck_sec}s in state {state_str}",
        duration_sec=0.0,
        reason="watchdog_stuck",
    )
    try:
        await artifact_checks.insert_check(
            pool, req_id, f"watchdog:{state_str}", check,
        )
    except Exception as e:
        log.warning("watchdog.artifact_insert_failed", req_id=req_id, error=str(e))

    # 3. 通过 engine.step 发 SESSION_FAILED → 走 escalate transition
    # 填 escalated_reason 给 escalate action 用
    await req_state.update_context(pool, req_id, {
        "escalated_reason": "watchdog-stuck-30min",
        "escalated_hint": f"stuck for {stuck_sec}s in state {state_str}",
    })

    body = _SyntheticBody(
        projectId=project_id,
        issueId=issue_id or ctx.get("intent_issue_id") or "",
    )
    log.warning(
        "watchdog.escalating",
        req_id=req_id, state=state_str,
        issue_id=issue_id, stuck_sec=stuck_sec,
    )
    try:
        await engine.step(
            pool,
            body=body,
            req_id=req_id,
            project_id=project_id,
            tags=[req_id, f"watchdog:{state_str}"],
            cur_state=state,
            ctx=ctx,
            event=Event.SESSION_FAILED,
        )
    except Exception as e:
        log.exception("watchdog.engine_step_failed", req_id=req_id, error=str(e))
        return "skip"
    return "escalated"


async def run_loop() -> None:
    """orchestrator 启动起的后台任务。"""
    if not settings.watchdog_enabled:
        log.info("watchdog.disabled")
        return
    interval = settings.watchdog_interval_sec
    log.info(
        "watchdog.loop.started",
        interval_sec=interval,
        stuck_threshold_sec=settings.watchdog_stuck_threshold_sec,
        warn_threshold_sec=_WARN_THRESHOLD_SEC,
    )
    while True:
        try:
            result = await _tick()
            if result.get("escalated") or result.get("warned"):
                log.warning("watchdog.swept", **result)
            else:
                log.debug("watchdog.tick", **result)
        except asyncio.CancelledError:
            log.info("watchdog.loop.stopped")
            raise
        except Exception as e:
            log.exception("watchdog.loop.error", error=str(e))
        await asyncio.sleep(interval)
