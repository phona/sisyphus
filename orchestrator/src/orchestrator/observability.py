"""tap：往 sisyphus_obs.event_log 插入事件流（替代旧 n8n tap 节点）。

设计原则（沿用旧设计）：
- best-effort：obs DB 慢/挂不能阻塞业务
- 永不抛异常：try/except 包外层
- timeout 短（pool 已 5s）

schema 见 observability/schema.sql（event_log）。
"""
from __future__ import annotations

import json

import structlog

from .router import _get_target, get_parent_id, get_parent_stage, get_round
from .store import db

log = structlog.get_logger(__name__)

# 把 issue tags → 推断 stage（与 router._infer 类似但收敛到一个枚举）
_STAGE_FROM_TAGS = (
    "analyze",
    "contract-test", "accept-test",
    "dev", "ci",
    "accept", "bugfix", "test-fix", "reviewer",
    "done-archive", "github-incident",
)


def infer_stage(tags: list[str]) -> str | None:
    for s in _STAGE_FROM_TAGS:
        if s in tags:
            return s
    return None


async def record_event(
    kind: str,
    *,
    req_id: str | None = None,
    issue_id: str | None = None,
    parent_issue_id: str | None = None,
    parent_stage: str | None = None,
    stage: str | None = None,
    tags: list[str] | None = None,
    round: int | None = None,
    target: str | None = None,
    router_action: str | None = None,
    router_reason: str | None = None,
    duration_ms: int | None = None,
    status_code: int | None = None,
    error_msg: str | None = None,
    extras: dict | None = None,
) -> None:
    """append 一行 event_log。tap 不抛 — DB 挂掉只 log warn。"""
    pool = db.get_obs_pool()
    if pool is None:
        return  # obs 没配，跳过

    # tag 自动派生 stage / round / target / parent_*（caller 没显式传时）
    tagset = list(tags) if tags else []
    if tagset:
        stage = stage or infer_stage(tagset)
        round = round if round is not None else (get_round(tagset) or None)
        target = target or _get_target(set(tagset))
        parent_issue_id = parent_issue_id or get_parent_id(tagset)
        parent_stage = parent_stage or get_parent_stage(tagset)

    sql = """
        INSERT INTO event_log (
            kind, req_id, stage, issue_id, parent_issue_id, parent_stage,
            tags, round, target,
            router_action, router_reason, duration_ms, status_code, error_msg,
            extras
        ) VALUES (
            $1,$2,$3,$4,$5,$6,
            $7,$8,$9,
            $10,$11,$12,$13,$14,
            $15
        )
    """
    try:
        await pool.execute(
            sql,
            kind, req_id, stage, issue_id, parent_issue_id, parent_stage,
            tagset or None, round, target,
            router_action, router_reason, duration_ms, status_code, error_msg,
            json.dumps(extras) if extras else None,
        )
    except Exception as e:
        log.warning("obs.tap_failed", kind=kind, error=str(e))
