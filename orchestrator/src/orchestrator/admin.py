"""Admin endpoints：手动驱状态机 + 强制处理卡住的 REQ + v0.2 runner 运维。

需要同样的 Authorization: Bearer <webhook_token> 头。

State 操作：
  POST /admin/req/{req_id}/emit       body: {"event": "..."}
  POST /admin/req/{req_id}/escalate
  POST /admin/req/{req_id}/complete   body: {"reason": "..."} (optional)
  POST /admin/req/{req_id}/resume     body: {"action": "pass"|"fix-needed", "stage"?, "fixer"?, "reason"?}
                                       → state-level resume from ESCALATED
                                       （派 VERIFY_PASS / VERIFY_FIX_NEEDED 走合法 transition）
  GET  /admin/metrics
  GET  /admin/req/{req_id}

v0.2 K8s runner 运维：
  POST /admin/req/{req_id}/runner-pause       → 删 Pod，PVC 保留
  POST /admin/req/{req_id}/runner-resume      → 重建 Pod
  POST /admin/req/{req_id}/rebuild-workspace  → 强拉代码重建 workspace（需 PVC 存在）
  GET  /admin/runners                          → 列所有 runner pod / pvc 状态
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import engine, k8s_runner
from .bkd import BKDClient
from .config import settings
from .state import Event, ReqState
from .store import db, req_state
from .webhook import _verify_token

log = structlog.get_logger(__name__)
admin = APIRouter(prefix="/admin")


class EmitBody(BaseModel):
    event: str


class _FakeBody:
    """伪 webhook body 喂 engine.step（没真实 webhook 但需要这些字段）。"""

    def __init__(self, req_id: str, project_id: str):
        self.issueId = f"admin-{req_id}"
        self.projectId = project_id
        self.event = "admin.inject"
        self.title = ""
        self.tags = []
        self.issueNumber = None


@admin.post("/req/{req_id}/emit")
async def emit_event(
    req_id: str,
    body: EmitBody,
    authorization: str | None = Header(default=None),
) -> dict:
    """手动注入一个状态机事件。"""
    _verify_token(authorization)

    try:
        ev = Event(body.event)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"unknown event '{body.event}'; valid: {[e.value for e in Event]}",
        ) from None

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    log.warning("admin.emit", req_id=req_id, event=body.event, from_state=row.state.value)
    fake = _FakeBody(req_id, row.project_id)
    return await engine.step(
        pool,
        body=fake,
        req_id=req_id,
        project_id=row.project_id,
        tags=[],
        cur_state=row.state,
        ctx=row.context,
        event=ev,
    )


class EscalateBody(BaseModel):
    """force_escalate body。kind 写入 ctx.escalated_reason + BKD reason tag。"""
    kind: str = "admin"


# 持引用防 fire-and-forget cleanup task 被 GC（done_callback 自清）。
# 跟 _complete_cleanup_tasks 同模式但隔离作用域，便于测试 introspect 仅本 endpoint 起的 task。
_force_escalate_cleanup_tasks: set[asyncio.Task] = set()


@admin.post("/req/{req_id}/escalate")
async def force_escalate(
    req_id: str,
    body: EscalateBody | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    """强制 REQ 进入 escalated（卡死时手工止损）+ 立即清 runner Pod（保 PVC 给人工 debug）。

    所有走状态机 transition 进 ESCALATED 的路径都会被 engine._cleanup_runner_on_terminal
    清掉 Pod（retain_pvc=True）。force_escalate 是 raw SQL UPDATE 绕过 engine，必须在
    这里手动起同一个 cleanup task，否则 Pod 会以 zombie 存活整个 pvc_retain_on_escalate_days
    保留期（runner_gc 把 escalated retention 内的 REQ 当 active，不扫 Pod）。
    """
    _verify_token(authorization)
    params = body or EscalateBody()

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    if row.state == ReqState.ESCALATED:
        return {"action": "noop", "state": "already escalated"}

    # 直接 SQL 强推（不走 CAS / engine，因为可能是任意 state）
    await pool.execute(
        "UPDATE req_state SET state='escalated', "
        "context = context || $2::jsonb, updated_at = now() WHERE req_id = $1",
        req_id,
        json.dumps({"escalated_reason": params.kind}),
    )

    # 立即触发 runner cleanup（不等 runner_gc 下一轮）；fire-and-forget。
    # retain_pvc=True 由 _cleanup_runner_on_terminal 内部根据 ESCALATED 自动设置，
    # 跟所有走 transition 进 ESCALATED 的路径行为一致：删 Pod，留 PVC 给人工 debug，
    # 过期由 runner_gc 按 pvc_retain_on_escalate_days 兜底清。
    task = asyncio.create_task(
        engine._cleanup_runner_on_terminal(req_id, ReqState.ESCALATED)
    )
    _force_escalate_cleanup_tasks.add(task)
    task.add_done_callback(_force_escalate_cleanup_tasks.discard)

    # BKD sync: tag intent issue + move to review（mirror escalate.py session-failed path）。
    # non-blocking — BKD 不可达不阻塞强推结果。
    intent_issue_id = (row.context or {}).get("intent_issue_id") or req_id
    proj = row.project_id
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            await bkd.merge_tags_and_update(
                proj, intent_issue_id,
                add=["escalated", f"reason:{params.kind}"],
                status_id="review",
            )
    except Exception as e:
        log.warning(
            "admin.force_escalate.bkd_sync_failed",
            req_id=req_id, intent_issue_id=intent_issue_id, error=str(e),
        )

    log.warning("admin.force_escalate", req_id=req_id, from_state=row.state.value, kind=params.kind)
    return {"action": "force_escalated", "from_state": row.state.value, "kind": params.kind}


class CompleteBody(BaseModel):
    """complete 接受可选的 reason 字段。"""
    reason: str | None = None


# 持引用防 fire-and-forget cleanup task 被 GC（done_callback 自清）。
# 跟 engine._cleanup_tasks 同模式但隔离作用域，便于测试 introspect 仅本 endpoint 起的 task。
_complete_cleanup_tasks: set[asyncio.Task] = set()


@admin.post("/req/{req_id}/complete")
async def complete_req(
    req_id: str,
    body: CompleteBody | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    """把 stale escalated REQ 直接标 done + 立即触发 runner cleanup（PVC 不留）。

    跟 force_escalate 配对：force_escalate 是"踢死信队列"，complete 是"清死信队列"。

    前置条件：
      - state == DONE: 200 noop（幂等）
      - state == ESCALATED: 改 done + cleanup
      - 其它 state: 409（防误把 in-flight stage 截断）
    """
    _verify_token(authorization)
    params = body or CompleteBody()

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    if row.state == ReqState.DONE:
        return {"action": "noop", "state": "already done"}

    if row.state != ReqState.ESCALATED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"req {req_id} is in state {row.state.value}; expected escalated. "
                f"Hint: POST /admin/req/{req_id}/escalate first if you want to "
                f"abort an in-flight REQ."
            ),
        )

    history_entry: dict = {
        "ts": datetime.now(UTC).isoformat(),
        "from": ReqState.ESCALATED.value,
        "to": ReqState.DONE.value,
        "event": "admin.complete",
        "action": None,
    }
    ctx_patch: dict = {
        "completed_reason": "admin",
        "completed_from_state": ReqState.ESCALATED.value,
    }
    if params.reason:
        ctx_patch["completed_reason_detail"] = params.reason

    # 直接 SQL UPDATE（mirror force_escalate）；WHERE state='escalated' 防并发竞争。
    result = await pool.execute(
        "UPDATE req_state SET state='done', "
        "history = history || $2::jsonb, "
        "context = context || $3::jsonb, "
        "updated_at = now() "
        "WHERE req_id = $1 AND state = $4",
        req_id,
        json.dumps([history_entry]),
        json.dumps(ctx_patch),
        ReqState.ESCALATED.value,
    )
    # asyncpg 返 "UPDATE N" 字符串；N=0 表示并发已被另一 caller 改了
    if not result.endswith(" 1"):
        log.warning("admin.complete.cas_lost", req_id=req_id, result=result)
        # 重读一次，按当前 state 决定回什么
        row2 = await req_state.get(pool, req_id)
        if row2 and row2.state == ReqState.DONE:
            return {"action": "noop", "state": "already done"}
        raise HTTPException(
            status_code=409,
            detail=f"req {req_id} state changed concurrently; retry",
        )

    # 立即触发 runner cleanup（不等 runner_gc 下一轮）；fire-and-forget。
    # engine._cleanup_runner_on_terminal 是 cross-module import：admin override
    # 复用同一套 cleanup 逻辑（log + try/except + retain_pvc 旗标），不复制。
    task = asyncio.create_task(
        engine._cleanup_runner_on_terminal(req_id, ReqState.DONE)
    )
    _complete_cleanup_tasks.add(task)
    task.add_done_callback(_complete_cleanup_tasks.discard)

    log.warning(
        "admin.complete",
        req_id=req_id,
        from_state=ReqState.ESCALATED.value,
        reason=params.reason,
    )
    return {
        "action": "completed",
        "from_state": ReqState.ESCALATED.value,
        "reason": params.reason,
    }


class ResumeBody(BaseModel):
    """state-level resume from ESCALATED 的 body schema。

    action 必传，无默认 —— 走 admin override 绕过 verifier-agent 的语义要求 explicit。
    其余可选：stage / fixer 覆盖 ctx 路由字段；reason 仅审计。
    """
    action: Literal["pass", "fix-needed"]
    stage: str | None = None
    fixer: Literal["dev", "spec"] | None = None
    reason: str | None = None


@admin.post("/req/{req_id}/resume")
async def resume_req(
    req_id: str,
    body: ResumeBody,
    authorization: str | None = Header(default=None),
) -> dict:
    """从 ESCALATED 派 verifier 决策 Event，走合法 transition 推进状态机。

    跟 BKD verifier follow-up 路径并行：两条路径都最终命中
    `(ESCALATED, VERIFY_PASS) → REVIEW_RUNNING (apply_verify_pass)` 或
    `(ESCALATED, VERIFY_FIX_NEEDED) → FIXER_RUNNING (start_fixer)`，
    区别仅在事件来源（BKD verifier 路径有 verifier_decisions 行；admin
    路径靠 ctx.resumed_by_admin 标识）。

    适用：
    - 已经知道答案（infra flake / agent 误判），跑 verifier 浪费 quota
    - ESCALATED 来自非 verifier 路径（pr_ci.timeout / accept-env-up.fail
      / intake.fail），没 verifier issue 给人 follow-up
    """
    _verify_token(authorization)

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    if row.state != ReqState.ESCALATED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"req {req_id} is in state {row.state.value}; expected escalated. "
                f"Hint: POST /admin/req/{req_id}/escalate first to abort an "
                f"in-flight REQ."
            ),
        )

    # action=pass 必须有可路由的 verifier_stage（apply_verify_pass 拿不到会
    # emit VERIFY_ESCALATE 走自循环，对调用方静默 —— 早 fail 给清晰错误）。
    effective_stage = body.stage or (row.context or {}).get("verifier_stage")
    if body.action == "pass" and not effective_stage:
        raise HTTPException(
            status_code=400,
            detail=(
                "verifier_stage required for action=pass; "
                "provide body.stage or use BKD verifier follow-up"
            ),
        )

    # ctx 预置：admin 标记 + 可选覆盖
    ctx_patch: dict = {
        "resumed_by_admin": True,
        "resume_action": body.action,
    }
    if body.stage:
        ctx_patch["verifier_stage"] = body.stage
    if body.fixer:
        ctx_patch["verifier_fixer"] = body.fixer
    if body.reason:
        ctx_patch["resume_reason"] = body.reason
    await req_state.update_context(pool, req_id, ctx_patch)

    # 重读 ctx，让 engine.step 收到 patched 后的版本
    row = await req_state.get(pool, req_id)
    if row is None:  # 极小概率：admin 调用前并发删 row
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    event = (
        Event.VERIFY_PASS if body.action == "pass" else Event.VERIFY_FIX_NEEDED
    )
    fake = _FakeBody(req_id, row.project_id)
    log.warning(
        "admin.resume",
        req_id=req_id, action=body.action, stage=effective_stage,
        fixer=body.fixer, reason=body.reason,
    )
    chained = await engine.step(
        pool,
        body=fake,
        req_id=req_id,
        project_id=row.project_id,
        tags=[],
        cur_state=row.state,
        ctx=row.context,
        event=event,
    )
    return {
        "action": "resumed",
        "from_state": ReqState.ESCALATED.value,
        "event": event.value,
        "chained": chained,
    }


@admin.get("/metrics")
async def metrics(
    authorization: str | None = Header(default=None),
) -> dict:
    """关键运行指标（JSON），就是 SQL view 的包装便于 curl / UI 拉。

    - 状态分布
    - per-stage 聚合（平均 + P50 + P95 + 次数，从 stage_stats view 来）
    - escalated REQ 的失败原因 Top N
    - 最近 20 个 REQ 简表
    """
    _verify_token(authorization)
    pool = db.get_pool()

    rows = await pool.fetch(
        "SELECT state, count(*) AS n FROM req_state GROUP BY state",
    )
    state_dist = {r["state"]: r["n"] for r in rows}

    rows = await pool.fetch(
        "SELECT stage, enter_count, req_count, avg_sec, p50_sec, p95_sec "
        "FROM stage_stats ORDER BY p95_sec DESC NULLS LAST",
    )
    stages = [
        {
            "stage": r["stage"],
            "enter_count": r["enter_count"],
            "req_count": r["req_count"],
            "avg_sec": float(r["avg_sec"]) if r["avg_sec"] is not None else None,
            "p50_sec": float(r["p50_sec"]) if r["p50_sec"] is not None else None,
            "p95_sec": float(r["p95_sec"]) if r["p95_sec"] is not None else None,
        }
        for r in rows
    ]

    rows = await pool.fetch(
        "SELECT reason, count, recent_reqs FROM failure_mode LIMIT 10",
    )
    failures = [
        {
            "reason": r["reason"],
            "count": r["count"],
            "recent_reqs": list(r["recent_reqs"]),
        }
        for r in rows
    ]

    rows = await pool.fetch(
        "SELECT req_id, final_state, total_sec, total_steps, bugfix_rounds, intent_title "
        "FROM req_summary ORDER BY updated_at DESC LIMIT 20",
    )
    recent = [dict(r) for r in rows]

    # agent 质量（来自 sisyphus_obs）—— 可选，obs DB 没配就跳过
    agents: list[dict] = []
    diagnosis: list[dict] = []
    obs_pool = db.get_obs_pool()
    if obs_pool is not None:
        try:
            arows = await obs_pool.fetch(
                "SELECT agent_role, total_invocations, distinct_reqs, "
                "avg_invocations_per_req, first_pass_pct, avg_duration_sec, "
                "result_pass, result_fail "
                "FROM agent_quality",
            )
            agents = [dict(r) for r in arows]
            drows = await obs_pool.fetch(
                "SELECT diagnosis, count FROM bugfix_diagnosis",
            )
            diagnosis = [dict(r) for r in drows]
        except Exception as e:
            log.warning("metrics.obs_query_failed", error=str(e))

    return {
        "state_distribution": state_dist,
        "stage_stats": stages,
        "failure_modes": failures,
        "recent_reqs": recent,
        "agent_quality": agents,
        "bugfix_diagnosis": diagnosis,
    }


@admin.get("/req/{req_id}")
async def get_req(
    req_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """读 REQ 状态 + 完整 history + ctx。"""
    _verify_token(authorization)

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    return {
        "req_id": row.req_id,
        "project_id": row.project_id,
        "state": row.state.value,
        "history": row.history,
        "context": row.context,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ═══════════════════════════════════════════════════════════════════════
# v0.2 runner 运维 endpoints
# ═══════════════════════════════════════════════════════════════════════


def _require_controller() -> k8s_runner.RunnerController:
    """没初始化就 503，不让 caller 以为成功了。"""
    try:
        return k8s_runner.get_controller()
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"k8s runner controller not initialized: {e}",
        ) from None


@admin.post("/req/{req_id}/runner-pause")
async def pause_runner(
    req_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """删 Pod（PVC 保留），释放 docker daemon / 节点内存资源；workspace 保留。

    适合"让出资源给高优 REQ"的场景。runner-resume 即恢复 Pod。

    路径加 `runner-` 前缀（曾经是 `/pause`），跟 state-level `/resume` 区分
    （后者派 verifier 决策 Event，跟 K8s 资源无关）。
    """
    _verify_token(authorization)
    rc = _require_controller()

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    deleted = await rc.pause(req_id)
    log.warning("admin.pause", req_id=req_id, pod_deleted=deleted)
    return {"action": "paused", "pod_deleted": deleted, "pvc_kept": True}


@admin.post("/req/{req_id}/runner-resume")
async def resume_runner(
    req_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """重建 Pod，PVC 自动重新挂载。

    路径加 `runner-` 前缀（曾经是 `/resume`），跟 state-level `/resume`
    （从 ESCALATED 派 verifier 决策 Event）区分。
    """
    _verify_token(authorization)
    rc = _require_controller()

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    pod_name = await rc.resume(req_id)
    log.warning("admin.resume", req_id=req_id, pod=pod_name)
    return {"action": "resumed", "pod": pod_name}


class RebuildBody(BaseModel):
    """rebuild workspace 的参数。"""
    keep_pvc: bool = True     # True = 保留 PVC 只重拉 git；False = 删 PVC 从零重建


@admin.post("/req/{req_id}/rebuild-workspace")
async def rebuild_workspace(
    req_id: str,
    body: RebuildBody | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    """强制重建 workspace。

    场景：
    - PVC 数据被误删 / 损坏
    - 手动调试需要从 branch 头重新开始

    行为：
    1. 删除当前 Pod
    2. 若 keep_pvc=False，也删 PVC（workspace 清零）
    3. 重建 Pod（PVC 保留则挂原 PVC；删了就挂新的空 PVC）
    4. 由下一个 stage 的 agent 自行检测 workspace 丢 → 重新 clone

    **不**触发状态机重走——REQ state 不变，只管 K8s 资源。
    """
    _verify_token(authorization)
    rc = _require_controller()
    params = body or RebuildBody()

    pool = db.get_pool()
    row = await req_state.get(pool, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"req {req_id} not found")

    if params.keep_pvc:
        # 只删 pod，重建（PVC 原样挂回）
        await rc.pause(req_id)
        pod_name = await rc.ensure_runner(req_id, wait_ready=True)
        action = "rebuilt_pod_pvc_kept"
    else:
        # 全销重建（PVC 也删）
        await rc.destroy(req_id)
        pod_name = await rc.ensure_runner(req_id, wait_ready=True)
        action = "rebuilt_pod_pvc_recreated"

    log.warning("admin.rebuild_workspace", req_id=req_id,
                action=action, state=row.state.value)
    return {"action": action, "pod": pod_name, "current_state": row.state.value}


@admin.get("/runners")
async def list_runners(
    authorization: str | None = Header(default=None),
) -> dict:
    """列 sisyphus-runners namespace 下所有 runner Pod + PVC 状态。"""
    _verify_token(authorization)
    rc = _require_controller()
    runners = await rc.list_runners()
    return {
        "count": len(runners),
        "runners": [
            {
                "req_id": r.req_id,
                "pod_name": r.pod_name,
                "pvc_name": r.pvc_name,
                "pod_phase": r.pod_phase,
                "pvc_phase": r.pvc_phase,
                "created_at": r.created_at,
            }
            for r in runners
        ],
    }
