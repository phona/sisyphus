"""M8 watchdog 后台任务：兜底卡死的 stage。

背景：dev BKD agent spawn 失败（session=failed 但 0 logs）时 BKD 不发
session.failed webhook，orchestrator 永远卡在 in-flight state 等不来事件。
M4 retry policy 假设"失败事件总会到"被打破 — watchdog 作为独立兜底。

每 N 秒扫一次 req_state，发现某 REQ：
1. state 在 in-flight（非 done / 非 escalated / 非 init）
2. updated_at 距今超过 watchdog_stuck_threshold_sec
3. 关联 BKD issue 的 session_status 不在 'running' 状态

→ 写一条 artifact_checks 记录 + 通过 engine.step 发 SESSION_FAILED 走 escalate。

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


# intake-agent 标记终态结果的 BKD tags（缺一不可：缺 → router 不 fire 主链事件）
_INTAKE_RESULT_TAGS: frozenset[str] = frozenset({"result:pass", "result:fail"})

# 专属 body.event：让 escalate.py 区分这条路径，绕开 watchdog.stuck 的 canonical
# 反向覆盖（保留 ctx.escalated_reason="intake-no-result-tag"）+ 跳过 auto-resume
_INTAKE_NO_RESULT_EVENT: str = "watchdog.intake_no_result_tag"
_INTAKE_NO_RESULT_REASON: str = "intake-no-result-tag"


# state → ctx 里追踪该 stage 当前 agent issue 的 key
# None 表示没 issue 可查（直接 escalate）
_STATE_ISSUE_KEY: dict[ReqState, str | None] = {
    ReqState.INTAKING: "intent_issue_id",        # intake-agent 复用 intent issue
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


@dataclass
class _SyntheticBody:
    """engine.step / escalate action 对 body 的最小字段依赖。"""
    projectId: str
    issueId: str
    event: str = "watchdog.stuck"


def _is_intake_no_result_tag(state: ReqState, issue) -> bool:
    """state=INTAKING + session 已结束（非 running）+ tags 缺 result:* → 卡死信号。

    intake-agent session.completed 后必须 PATCH `result:pass` / `result:fail` tag
    让 router 派 INTAKE_PASS/FAIL；忘了打 tag 会导致 webhook 永不 fire 主链事件，
    REQ 永远卡在 INTAKING。识别此条件就 escalate（不 retry —— session 已 done，
    auto-resume 也唤不醒已结束的 agent）。
    """
    if state != ReqState.INTAKING or issue is None:
        return False
    if issue.session_status == "running":
        return False
    return not (set(issue.tags or []) & _INTAKE_RESULT_TAGS)


async def _tick() -> dict:
    """单次扫描 + escalate 卡死 REQ。返回 {checked, escalated}。"""
    pool = db.get_pool()
    threshold = settings.watchdog_stuck_threshold_sec
    # psql 语法：INTERVAL '1 second' * N 把 int 参数转成 interval
    rows = await pool.fetch(
        """
        SELECT req_id, project_id, state, context,
               EXTRACT(EPOCH FROM (NOW() - updated_at))::BIGINT AS stuck_sec
          FROM req_state
         WHERE state <> ALL($1::text[])
           AND updated_at < NOW() - INTERVAL '1 second' * $2
        """,
        list(_SKIP_STATES), threshold,
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
    issue = None
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

    # 2. 区分细分 stuck reason：
    #    - INTAKING + session 已结束 + 无 result tag → intake-no-result-tag
    #    - 其他 → 通用 watchdog_stuck
    pool = db.get_pool()
    intake_no_result = _is_intake_no_result_tag(state, issue)
    if intake_no_result:
        reason = _INTAKE_NO_RESULT_REASON
        body_event = _INTAKE_NO_RESULT_EVENT
        stage_label = "watchdog:intake-no-result-tag"
        stderr_tail = (
            f"intake session ended (status={issue.session_status}) "
            f"without result:pass/result:fail tag; stuck for {stuck_sec}s"
        )
    else:
        body_event = _STATE_FAILURE_EVENT.get(state, "watchdog.stuck")
        reason = "watchdog_stuck"
        stage_label = f"watchdog:{state_str}"
        stderr_tail = f"stuck for {stuck_sec}s in state {state_str}"

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

    # 4. intake-no-result-tag：把 reason 提前落 ctx，escalate.py 经
    #    "非 canonical body.event → ctx.escalated_reason 优先" 通路读到，
    #    最终 BKD tag = `reason:intake-no-result-tag`（而非 `reason:watchdog-stuck`）。
    if intake_no_result:
        try:
            await req_state.update_context(
                pool, req_id, {"escalated_reason": reason},
            )
        except Exception as e:
            log.warning(
                "watchdog.ctx_update_failed", req_id=req_id, error=str(e),
            )
        ctx = {**ctx, "escalated_reason": reason}

    # 5. 通过 engine.step 发 SESSION_FAILED → 走 escalate transition
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
