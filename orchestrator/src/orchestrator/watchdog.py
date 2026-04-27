"""M8 watchdog 后台任务：兜底卡死的 stage。

背景：dev BKD agent spawn 失败（session=failed 但 0 logs）时 BKD 不发
session.failed webhook，orchestrator 永远卡在 in-flight state 等不来事件。
M4 retry policy 假设"失败事件总会到"被打破 — watchdog 作为独立兜底。

每 N 秒扫一次 req_state，发现某 REQ：
1. state 在 in-flight（非 done / 非 escalated / 非 init / 非 human-in-loop）
2. updated_at 距今超过 SQL 预滤阈值（min over 所有 stage policy 的 ended/stuck）
3. 关联 BKD issue 的 session_status 不在 'running' 状态 OR 触发本 stage 慢车道

→ 写一条 artifact_checks 记录 + 通过 engine.step 发 SESSION_FAILED 走 escalate。

REQ-bkd-analyze-hang-debug-1777247423（2026-04-27）：拆出 ended-session
fast lane —— SQL 预滤用 min(ended, stuck) 让 BKD 报已结束的 session 在
~5min 内被兜底，而不是等满 60min；session_status=='running' 的行仍由
in-loop `still_running → return` 无条件 skip，保护长尾真分析。

REQ-watchdog-stage-policy-1777269909（2026-04-27）：按 stage type 走差异化
策略 —— `_NO_WATCHDOG_STATES` 收 human-in-loop state（目前仅 INTAKING），
SQL 预滤直接跳过它们，机械层不杀人在思考的 stage。

REQ-stage-watchdog-policy-full-1777280786（2026-04-27）：把 INTAKING-only
exempt set 升级成 stage-typed `_STAGE_POLICY` 表，每 stage 两个轴
（ended_sec / stuck_sec）。`_NO_WATCHDOG_STATES` 退化为派生集合（policy is None
的 stage）。unmapped state fallback 全局阈值，避免新增 ReqState 漏配时裸奔。
详见 docs/user-feedback-loop.md §1。

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


@dataclass(frozen=True)
class _StagePolicy:
    """REQ-stage-watchdog-policy-full-1777280786：单 stage 的 watchdog 策略。

    两个轴解耦"BKD session 已 ended"和"长尾真死"两种 escalate 触发条件：

    - ended_sec: BKD 报 session_status != "running"（completed/failed/cancelled/
      issue 不存在）后等多久 escalate。本质是"BKD agent 死了但 webhook 没到"
      的检测延迟。所有非豁免 stage 必须设。
    - stuck_sec: 不论 session 状态，stuck 超过该时长一律 escalate。`None` =
      不开启慢车道（保留"BKD running session 永远不杀"的长尾保护语义）。
      设具体数值则相当于"哪怕 BKD 还在 running，超时也认账"，目前仅
      external-poll 的 PR_CI_RUNNING 配 4h 上限。
    """
    ended_sec: int
    stuck_sec: int | None


# REQ-stage-watchdog-policy-full-1777280786：per-stage typed policy 表。
# 默认值依据 docs/user-feedback-loop.md §1 stage type taxonomy：
# - human-loop-conversation → None（SQL 预过滤）
# - deterministic-checker   → ended=300, stuck=300（除 STAGING_TEST 见下）
# - autonomous-bounded      → ended=300, stuck=None（保留不杀长尾，30min hard
#                             cap 留给运维数据驱动决策；config.py:186 写过
#                             30min 实测 false-escalate 长尾 sonnet ANALYZING）
# - external-poll           → ended=300, stuck=14400（4h CI hard cap）
#
# STAGING_TEST_RUNNING 名义是 deterministic-checker（kubectl exec），但单/集成
# 测试整套常跑分钟级，特别归"宽松 deterministic"档（stuck=None），避免误杀。
_STAGE_POLICY: dict[ReqState, _StagePolicy | None] = {
    # human-loop-conversation
    ReqState.INTAKING: None,
    # deterministic-checker（紧 5min ended + 5min stuck 双线）
    ReqState.SPEC_LINT_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=300),
    ReqState.DEV_CROSS_CHECK_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=300),
    ReqState.ANALYZE_ARTIFACT_CHECKING: _StagePolicy(ended_sec=300, stuck_sec=300),
    # deterministic-checker（宽松：测试套件常跑长）
    ReqState.STAGING_TEST_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=None),
    # autonomous-bounded
    ReqState.ANALYZING: _StagePolicy(ended_sec=300, stuck_sec=None),
    ReqState.CHALLENGER_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=None),
    ReqState.ACCEPT_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=None),
    ReqState.ACCEPT_TEARING_DOWN: _StagePolicy(ended_sec=300, stuck_sec=None),
    ReqState.ARCHIVING: _StagePolicy(ended_sec=300, stuck_sec=None),
    ReqState.FIXER_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=None),
    ReqState.REVIEW_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=None),
    # external-poll
    ReqState.PR_CI_RUNNING: _StagePolicy(ended_sec=300, stuck_sec=14400),
}


# 派生：policy is None 的 stage 集合，SQL 预过滤直接跳过。
# 其他模块（含测试）可能 import 这个 set，保留导出并保持语义不变。
_NO_WATCHDOG_STATES: frozenset[ReqState] = frozenset(
    s for s, p in _STAGE_POLICY.items() if p is None
)


def _resolve_policy(state: ReqState) -> _StagePolicy | None:
    """REQ-stage-watchdog-policy-full-1777280786：解析 stage 的 watchdog policy。

    - 表里显式列了 → 直接用（含 None / 显式 _StagePolicy）
    - 表里没列    → 用全局 `watchdog_session_ended_threshold_sec` +
                    `watchdog_stuck_threshold_sec` 合成 fallback policy。
                    安全网兜底：新加 ReqState 忘补表也不会裸奔无 watchdog。
    """
    if state in _STAGE_POLICY:
        return _STAGE_POLICY[state]
    return _StagePolicy(
        ended_sec=settings.watchdog_session_ended_threshold_sec,
        stuck_sec=settings.watchdog_stuck_threshold_sec,
    )


def _sql_prefilter_threshold() -> int:
    """SQL 预过滤的最低 stuck 阈值。

    取 `min(所有非 None policy 的 ended_sec ∪ stuck_sec ∪ 全局 fallback ended/stuck)`。
    保证任一 stage 的 escalate 触发条件都能让对应 row 进入 SQL 返回集（per-row
    逻辑再按 stage policy 精判）。
    """
    candidates: list[int] = [
        settings.watchdog_session_ended_threshold_sec,
        settings.watchdog_stuck_threshold_sec,
    ]
    for p in _STAGE_POLICY.values():
        if p is None:
            continue
        candidates.append(p.ended_sec)
        if p.stuck_sec is not None:
            candidates.append(p.stuck_sec)
    return min(candidates)


@dataclass
class _SyntheticBody:
    """engine.step / escalate action 对 body 的最小字段依赖。"""
    projectId: str
    issueId: str
    event: str = "watchdog.stuck"


async def _tick() -> dict:
    """单次扫描 + escalate 卡死 REQ。返回 {checked, escalated}。"""
    pool = db.get_pool()
    threshold = _sql_prefilter_threshold()
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
    """检查一条 stuck row，按 stage policy 决定 skip / escalate。返 True = 真 escalate。"""
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

    policy = _resolve_policy(state)
    if policy is None:
        # belt-and-suspenders：SQL 预过滤已排除，这里再保险
        log.debug(
            "watchdog.policy_exempt", req_id=req_id, state=state_str,
            stuck_sec=stuck_sec,
        )
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
        except Exception as e:
            # 查不到（issue 删了 / BKD 挂了）→ 保守按 ended 处理（走 ended_sec 阈值）
            log.warning(
                "watchdog.bkd_get_issue_failed",
                req_id=req_id, issue_id=issue_id, error=str(e),
            )

    # 2. 按 policy 决定是否 escalate
    if still_running:
        if policy.stuck_sec is None or stuck_sec < policy.stuck_sec:
            # 慢车道未开启或未到 → 不杀长尾运行 session
            log.debug(
                "watchdog.still_running",
                req_id=req_id, state=state_str,
                issue_id=issue_id, stuck_sec=stuck_sec,
                stuck_cap=policy.stuck_sec,
            )
            return False
        # 慢车道触发：BKD running 但已超过 stage 设的 stuck 上限
    else:
        # session ended 或无 issue：走 ended_sec 快车道
        if stuck_sec < policy.ended_sec:
            log.debug(
                "watchdog.below_ended_threshold",
                req_id=req_id, state=state_str,
                stuck_sec=stuck_sec, ended_cap=policy.ended_sec,
            )
            return False

    # 3. 选 body.event：ARCHIVING 用专属 archive.failed，其他通用 watchdog.stuck
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

    # 4. 写 artifact_checks 记一笔，给 dashboard M7 04-fail-kind-distribution 抓
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
