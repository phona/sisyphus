"""Admin endpoints：手动驱状态机 + 强制处理卡住的 REQ。

需要同样的 Authorization: Bearer <webhook_token> 头。

POST /admin/req/{req_id}/emit
   body: {"event": "ci-int.pass"}    # Event 枚举值
   → 给 REQ 注入一个事件，让 engine 试 transition

POST /admin/req/{req_id}/escalate
   → 强制 state=escalated，标记 escalated_reason=admin

POST /admin/req/{req_id}/cancel
   → 同 escalate + 提示外部清理 container/volume
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import engine
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


@admin.get("/metrics")
async def metrics(
    authorization: str | None = Header(default=None),
) -> dict:
    """关键运行指标（JSON）。curl / Prometheus textfile 都能用。

    - 状态分布
    - per-stage 聚合（平均 + P50 + P95 + 次数，从 stage_stats view 来）
    - escalated REQ 的失败原因 Top N
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

    return {
        "state_distribution": state_dist,
        "stage_stats": stages,
        "failure_modes": failures,
        "recent_reqs": recent,
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
