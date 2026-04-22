"""M4 执行器：按 policy.decide 的结果操作 BKD / 推进状态。

调用方（M1/M2/M3 checker fail 路径）把 RetryContext 传进来，executor 内部：
1. 读 ctx.retries[stage] + 1 = 本次 round（持久化到 req_state.context.retries）
2. policy.decide 得到 RetryDecision
3. 按 action 分发：
   - follow_up: BKDClient.follow_up_issue（同 agent 附错误详情）
   - fresh_start: cancel 旧 issue + 新开 issue + 摘要 prompt
   - diagnose: 新开 diagnose issue（tag 里带 stage）
   - skip_check_retry: 返回 hint 给调用方自己重跑
   - escalate: emit SESSION_FAILED 事件

返回 dict 给上层 action handler；含 `emit` 就让 engine 链式推进，无 emit 则
state 原地不动等下一轮。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db
from . import store as retry_store
from .policy import RetryDecision, decide

log = structlog.get_logger(__name__)


@dataclass
class RetryContext:
    """M4 executor 入参：caller 持有的所有必要信息。"""

    req_id: str
    project_id: str
    stage: str                      # checker 标识（"staging-test" / "pr-ci" / ...）
    fail_kind: str                  # policy 可识别的分类
    issue_id: str | None = None     # 要 follow_up / cancel 的 agent issue
    details: dict = field(default_factory=dict)   # cmd / exit_code / stdout_tail / stderr_tail / ...


async def run(ctx: RetryContext) -> dict:
    """执行一次重试决策，返回上层 action handler 直接用的 dict。

    返回 dict 含 `emit` 时，engine 会链式推进状态机；不含 `emit` 时 state
    原地留在当前（比如 STAGING_TEST_RUNNING），等后续 agent session 回调
    再驱动。
    """
    pool = db.get_pool()
    round = await retry_store.increment_round(pool, ctx.req_id, ctx.stage)
    decision = decide(
        ctx.stage, ctx.fail_kind, round,
        max_rounds=settings.retry_max_rounds,
        diagnose_threshold=settings.retry_diagnose_threshold,
    )
    log.info(
        "retry.decide",
        req_id=ctx.req_id, stage=ctx.stage, fail_kind=ctx.fail_kind,
        round=round, action=decision.action, reason=decision.reason,
    )

    dispatcher = _DISPATCH.get(decision.action)
    if dispatcher is None:
        log.error("retry.unknown_action", action=decision.action)
        return {"action": "error", "reason": f"unknown decision {decision.action}"}

    return await dispatcher(ctx, decision, round)


async def reset_stage(req_id: str, stage: str) -> None:
    """admission pass 后清零某 stage 的 round 计数。"""
    await retry_store.reset_round(db.get_pool(), req_id, stage)


async def _follow_up(ctx: RetryContext, decision: RetryDecision, round: int) -> dict:
    if not ctx.issue_id:
        log.warning("retry.follow_up_no_issue_id", req_id=ctx.req_id, stage=ctx.stage)
        return {
            "retry_action": "follow_up",
            "stage": ctx.stage, "round": round,
            "skipped": True, "reason": "missing issue_id; cannot follow_up",
        }
    prompt = render(
        "retry_follow_up.md.j2",
        req_id=ctx.req_id, stage=ctx.stage,
        fail_kind=ctx.fail_kind, round=round,
        details=ctx.details,
    )
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        await bkd.follow_up_issue(
            project_id=ctx.project_id, issue_id=ctx.issue_id, prompt=prompt,
        )
    return {
        "retry_action": "follow_up",
        "stage": ctx.stage, "round": round,
        "issue_id": ctx.issue_id, "reason": decision.reason,
    }


async def _fresh_start(ctx: RetryContext, decision: RetryDecision, round: int) -> dict:
    """prompt_too_long：cancel 旧 issue + 开新 issue，带摘要 prompt。"""
    prompt = render(
        "retry_fresh_start.md.j2",
        req_id=ctx.req_id, stage=ctx.stage, round=round,
        details=ctx.details,
    )
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        if ctx.issue_id:
            try:
                await bkd.cancel_issue(ctx.project_id, ctx.issue_id)
            except Exception as e:
                # cancel 失败不阻塞重开；老 session 自己最终 timeout
                log.warning(
                    "retry.fresh_start.cancel_failed",
                    req_id=ctx.req_id, issue_id=ctx.issue_id, error=str(e),
                )
        new_issue = await bkd.create_issue(
            project_id=ctx.project_id,
            title=f"[{ctx.req_id}] [{ctx.stage}][retry-r{round}]",
            tags=[ctx.stage, ctx.req_id, f"retry:r{round}"],
            status_id="todo",
        )
        await bkd.follow_up_issue(
            project_id=ctx.project_id, issue_id=new_issue.id, prompt=prompt,
        )
        await bkd.update_issue(
            project_id=ctx.project_id, issue_id=new_issue.id, status_id="working",
        )
    return {
        "retry_action": "fresh_start",
        "stage": ctx.stage, "round": round,
        "new_issue_id": new_issue.id, "reason": decision.reason,
    }


async def _diagnose(ctx: RetryContext, decision: RetryDecision, round: int) -> dict:
    """≥diagnose_threshold 轮测试失败：起轻量 diagnose agent 分流。"""
    prompt = render(
        "retry_diagnose.md.j2",
        req_id=ctx.req_id, stage=ctx.stage, round=round,
        details=ctx.details,
    )
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        diag_issue = await bkd.create_issue(
            project_id=ctx.project_id,
            title=f"[{ctx.req_id}] [diagnose][{ctx.stage}]",
            tags=["diagnose", ctx.stage, ctx.req_id, f"retry:r{round}"],
            status_id="todo",
        )
        await bkd.follow_up_issue(
            project_id=ctx.project_id, issue_id=diag_issue.id, prompt=prompt,
        )
        await bkd.update_issue(
            project_id=ctx.project_id, issue_id=diag_issue.id, status_id="working",
        )
    return {
        "retry_action": "diagnose",
        "stage": ctx.stage, "round": round,
        "diagnose_issue_id": diag_issue.id, "reason": decision.reason,
    }


async def _skip_check_retry(ctx: RetryContext, decision: RetryDecision, round: int) -> dict:
    """flaky：sisyphus 自己重跑 check，不烦 agent。caller 检测 hint 后再次 run_checker。"""
    return {
        "retry_action": "skip_check_retry",
        "stage": ctx.stage, "round": round,
        "reason": decision.reason,
        "hint": "caller should re-run the checker",
    }


async def _escalate(ctx: RetryContext, decision: RetryDecision, round: int) -> dict:
    """超过 max_rounds 或未知 fail_kind：emit SESSION_FAILED 进 escalate 路径。"""
    return {
        "emit": Event.SESSION_FAILED.value,
        "retry_action": "escalate",
        "stage": ctx.stage, "round": round,
        "reason": decision.reason,
        "escalated_at": time.time(),
    }


_DISPATCH = {
    "follow_up": _follow_up,
    "fresh_start": _fresh_start,
    "diagnose": _diagnose,
    "skip_check_retry": _skip_check_retry,
    "escalate": _escalate,
}
