"""M8 watchdog 后台任务：兜底卡死的 stage。

背景：dev BKD agent spawn 失败（session=failed 但 0 logs）时 BKD 不发
session.failed webhook，orchestrator 永远卡在 in-flight state 等不来事件。
M4 retry policy 假设"失败事件总会到"被打破 — watchdog 作为独立兜底。

每 N 秒扫一次 req_state，发现某 REQ：
1. state 在 in-flight（非 done / 非 escalated / 非 init / 非 human-in-loop）
2. updated_at 距今超过 min(ended_threshold, stuck_threshold)（SQL 预滤）
3. 关联 BKD issue 的 session_status 不在 'running' 状态

→ 写一条 artifact_checks 记录 + 通过 engine.step 发 SESSION_FAILED 走 escalate。

REQ-bkd-analyze-hang-debug-1777247423（2026-04-27）：拆出 ended-session
fast lane —— SQL 预滤用 min(ended, stuck) 让 BKD 报已结束的 session 在
~5min 内被兜底，而不是等满 60min；session_status=='running' 的行仍由
in-loop `still_running → return` 无条件 skip，保护长尾真分析。

REQ-watchdog-stage-policy-1777269909（2026-04-27）：按 stage type 走差异化
策略 —— `_NO_WATCHDOG_STATES` 收 human-in-loop state（目前仅 INTAKING），
SQL 预滤直接跳过它们，机械层不杀人在思考的 stage。

不 restart agent（restart 归 M4 retry policy 管），只 escalate。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import structlog

from . import engine
from .bkd import BKDClient
from .checkers._types import CheckResult
from .config import settings
from .state import Event, ReqState
from .store import artifact_checks, db, req_state

log = structlog.get_logger(__name__)


# state → ctx 里追踪该 stage 当前 agent issue 的 key
# None 表示没 issue 可查（直接 escalate）
_STATE_ISSUE_KEY: dict[ReqState, str | None] = {
    ReqState.ANALYZING: "intent_issue_id",
    ReqState.ANALYZE_ARTIFACT_CHECKING: None,    # 客观 checker，由 orchestrator 下发不绑 issue
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

# state → 兜底失败时贴在 _SyntheticBody.event 上的字符串。
# escalate.py 把这串当 canonical signal，slug 化成 reason tag
# （如 "archive.failed" → "archive-failed"），让 dashboards 能区分
# done-archive 阶段崩溃 vs 通用 watchdog 卡死。
# 未列出的 state 默认用 "watchdog.stuck"（→ reason "watchdog-stuck"）。
_STATE_FAILURE_EVENT: dict[ReqState, str] = {
    ReqState.ARCHIVING: "archive.failed",
}

# 排除：终态 + 等人态 + 未入链
_SKIP_STATES = {
    ReqState.DONE.value,
    ReqState.ESCALATED.value,
    ReqState.GH_INCIDENT_OPEN.value,
    ReqState.INIT.value,
}

# REQ-watchdog-stage-policy-1777269909：human-in-loop stages 完全豁免 watchdog。
# 这类 stage 的进程靠人推（多轮 chat 等用户回复），机械超时杀掉是错的；人若
# 觉得真死了走 admin/resume 终止。INTAKING 是当前唯一这类 state。
_NO_WATCHDOG_STATES: frozenset[ReqState] = frozenset({
    ReqState.INTAKING,
})


@dataclass
class _SyntheticBody:
    """engine.step / escalate action 对 body 的最小字段依赖。"""
    projectId: str
    issueId: str
    event: str = "watchdog.stuck"


async def _tick() -> dict:
    """单次扫描 + escalate 卡死 REQ。返回 {checked, escalated}。"""
    pool = db.get_pool()
    # SQL 预滤用两个阈值的较小值。fast (ended_threshold) 让 BKD 已结束的
    # session 在 ~5min 内被兜底；slow (stuck_threshold) 是 legacy 上限。
    # 仍 running 的 session 由 _check_and_escalate 里的 still_running → skip
    # 无条件保护，不会被 fast lane 误伤。
    threshold = min(
        settings.watchdog_session_ended_threshold_sec,
        settings.watchdog_stuck_threshold_sec,
    )
    # 终态 / 未入链 + human-in-loop 一起塞进 SQL skip 列表
    skip_arr = list(_SKIP_STATES | {s.value for s in _NO_WATCHDOG_STATES})
    # psql 语法：INTERVAL '1 second' * N 把 int 参数转成 interval
    rows = await pool.fetch(
        """
        SELECT req_id, project_id, state, context,
               EXTRACT(EPOCH FROM (NOW() - updated_at))::BIGINT AS stuck_sec
          FROM req_state
         WHERE state <> ALL($1::text[])
           AND updated_at < NOW() - INTERVAL '1 second' * $2
        """,
        skip_arr, threshold,
    )
    escalated = 0
    for row in rows:
        if await _check_and_escalate(row):
            escalated += 1
    return {"checked": len(rows), "escalated": escalated}


async def _check_and_escalate(row) -> bool:
    """检查一条 stuck row：session 仍 running 就 skip，否则 escalate。返 True = 真 escalate。"""
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
        return False

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
            if issue.session_status == "running":
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
        return False

    # 2. 选 body.event：ARCHIVING 用专属 archive.failed，其他通用 watchdog.stuck
    body_event = _STATE_FAILURE_EVENT.get(state, "watchdog.stuck")
    reason = "watchdog_stuck"
    stage_label = f"watchdog:{state_str}"
    stderr_tail = f"stuck for {stuck_sec}s in state {state_str}"

    # 防 verifier↔fixer 死循环兜底：FIXER_RUNNING 卡住且 fixer_round 已达 cap →
    # 显式标 escalated_reason=fixer-round-cap，escalate.py 识别为 hard reason 直接终止。
    fx_round = int(ctx.get("fixer_round") or 0)
    cap = settings.fixer_round_cap
    pool = db.get_pool()
    if state == ReqState.FIXER_RUNNING and fx_round >= cap:
        try:
            await req_state.update_context(pool, req_id, {
                "escalated_reason": "fixer-round-cap",
                "fixer_round_cap_hit": cap,
            })
            ctx["escalated_reason"] = "fixer-round-cap"
            log.warning("watchdog.fixer_round_cap_hit",
                        req_id=req_id, fixer_round=fx_round, cap=cap)
        except Exception as e:
            log.warning("watchdog.fixer_round_cap_tag_failed",
                        req_id=req_id, error=str(e))

    # 3. 写 artifact_checks 记一笔，给 dashboard M7 04-fail-kind-distribution 抓
    check = CheckResult(
        passed=False,
        exit_code=-1,
        cmd=stage_label,
        stdout_tail="",
        stderr_tail=stderr_tail,
        duration_sec=0.0,
        reason=reason,
    )
    try:
        await artifact_checks.insert_check(pool, req_id, stage_label, check)
    except Exception as e:
        log.warning("watchdog.artifact_insert_failed", req_id=req_id, error=str(e))

    # 4. 通过 engine.step 发 SESSION_FAILED → 走 escalate transition
    body = _SyntheticBody(
        projectId=project_id,
        issueId=issue_id or ctx.get("intent_issue_id") or "",
        event=body_event,
    )
    log.warning(
        "watchdog.escalating",
        req_id=req_id, state=state_str,
        issue_id=issue_id, stuck_sec=stuck_sec,
        reason=reason,
    )
    try:
        await engine.step(
            pool,
            body=body,
            req_id=req_id,
            project_id=project_id,
            tags=[req_id, stage_label],
            cur_state=state,
            ctx=ctx,
            event=Event.SESSION_FAILED,
        )
    except Exception as e:
        log.exception("watchdog.engine_step_failed", req_id=req_id, error=str(e))
        return False
    return True


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
    )
    while True:
        try:
            result = await _tick()
            if result.get("escalated"):
                log.warning("watchdog.swept", **result)
            else:
                log.debug("watchdog.tick", **result)
        except asyncio.CancelledError:
            log.info("watchdog.loop.stopped")
            raise
        except Exception as e:
            log.exception("watchdog.loop.error", error=str(e))
        await asyncio.sleep(interval)
