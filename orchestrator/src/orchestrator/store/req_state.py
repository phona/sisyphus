"""REQ 状态 CRUD（CAS 推进）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

from ..state import Event, ReqState


@dataclass
class ReqRow:
    req_id: str
    project_id: str
    state: ReqState
    history: list[dict]
    context: dict
    created_at: datetime
    updated_at: datetime


async def get(pool: asyncpg.Pool, req_id: str) -> ReqRow | None:
    row = await pool.fetchrow(
        "SELECT req_id, project_id, state, history, context, created_at, updated_at "
        "FROM req_state WHERE req_id = $1",
        req_id,
    )
    if row is None:
        return None
    return ReqRow(
        req_id=row["req_id"],
        project_id=row["project_id"],
        state=ReqState(row["state"]),
        history=json.loads(row["history"]) if isinstance(row["history"], str) else (row["history"] or []),
        context=json.loads(row["context"]) if isinstance(row["context"], str) else (row["context"] or {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def insert_init(pool: asyncpg.Pool, req_id: str, project_id: str, context: dict | None = None) -> None:
    """新 REQ 落地，state=init。冲突直接 ignore（CAS transition 自己处理）。"""
    await pool.execute(
        "INSERT INTO req_state(req_id, project_id, state, context) VALUES($1, $2, $3, $4) "
        "ON CONFLICT (req_id) DO NOTHING",
        req_id, project_id, ReqState.INIT.value, json.dumps(context or {}),
    )


async def cas_transition(
    pool: asyncpg.Pool,
    req_id: str,
    expected_state: ReqState,
    next_state: ReqState,
    event: Event,
    action: str | None,
    context_patch: dict | None = None,
) -> bool:
    """CAS 推进 state：仅当当前 state == expected 时改 next_state，原子。

    返回 True = 成功推进；False = 状态已被别的并发事件改变，调用方应 skip。
    """
    history_entry = {
        "ts": datetime.now(UTC).isoformat(),
        "from": expected_state.value,
        "to": next_state.value,
        "event": event.value,
        "action": action,
    }
    # JSON merge for context_patch
    if context_patch:
        merged_ctx_sql = "context || $5::jsonb"
        ctx_param = json.dumps(context_patch)
    else:
        merged_ctx_sql = "context"
        ctx_param = "{}"  # placeholder, not used; pass anyway to keep signature

    sql = f"""
        UPDATE req_state
        SET state = $3,
            history = history || $4::jsonb,
            context = {merged_ctx_sql},
            updated_at = now()
        WHERE req_id = $1 AND state = $2
        RETURNING req_id
    """
    row = await pool.fetchrow(sql, req_id, expected_state.value, next_state.value,
                              json.dumps([history_entry]), ctx_param)
    return row is not None


async def update_context(pool: asyncpg.Pool, req_id: str, patch: dict) -> None:
    """单纯打补丁到 context（不改 state）。用于 action 完成后回写"创建了 issue X"等。"""
    await pool.execute(
        "UPDATE req_state SET context = context || $2::jsonb, updated_at = now() WHERE req_id = $1",
        req_id, json.dumps(patch),
    )
