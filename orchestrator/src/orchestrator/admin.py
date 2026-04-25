"""Admin endpoints：手动驱状态机 + 强制处理卡住的 REQ + v0.2 runner 运维。

需要同样的 Authorization: Bearer <webhook_token> 头。

基础（已有）：
  POST /admin/req/{req_id}/emit       body: {"event": "..."}
  POST /admin/req/{req_id}/escalate
  POST /admin/req/{req_id}/complete   body: {"reason": "..."} (optional)
  GET  /admin/metrics
  GET  /admin/req/{req_id}

v0.2 runner 运维（新）：
  POST /admin/req/{req_id}/pause      → 删 Pod，PVC 保留
  POST /admin/req/{req_id}/resume     → 重建 Pod
  POST /admin/req/{req_id}/rebuild-workspace  → 强拉代码重建 workspace（需 PVC 存在）
  GET  /admin/runners                  → 列所有 runner pod / pvc 状态
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import engine, k8s_runner
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


@admin.post("/req/{req_id}/escalate")
async def force_escalate(
    req_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """强制 REQ 进入 escalated（卡死时手工止损）。"""
    _verify_token(authorization)

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
        '{"escalated_reason": "admin"}',
    )
    log.warning("admin.force_escalate", req_id=req_id, from_state=row.state.value)
    return {"action": "force_escalated", "from_state": row.state.value}


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


@admin.post("/req/{req_id}/pause")
async def pause_runner(
    req_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """删 Pod（PVC 保留），释放 docker daemon / 节点内存资源；workspace 保留。

    适合"让出资源给高优 REQ"的场景。resume 即恢复。
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


@admin.post("/req/{req_id}/resume")
async def resume_runner(
    req_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """重建 Pod，PVC 自动重新挂载。"""
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
