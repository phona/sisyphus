"""bkd_snapshot 同步 + intent:analyze orphan 恢复。

两件事跑在同一个 5min 后台 task：

1. **bkd_snapshot UPSERT**（观测系数据）：定时拉每个已知 project 的 BKD list-issues，
   写到 `sisyphus_obs.bkd_snapshot`。这块只在 obs pool 配置时跑。

2. **intent:analyze orphan 恢复**（恢复路径）：webhook 是 INTENT_ANALYZE 的唯一入口；
   如果用户打 `intent:analyze` tag 那一发 webhook 在 sisyphus 重启 / BKD 抖动时丢了，
   REQ 就永远卡在没 req_state 行的状态。snapshot 顺手扫一下，发现 BKD 有 intent:analyze
   tag 但 req_state 没行的 issue，自己合成 webhook body 调 engine.step 把 INTENT_ANALYZE
   补发出去。这块跟 obs pool 无关，永远跑。

后台 asyncio task，main.py startup 时启动。失败只 log 不挂主流程。

多副本注意：N>1 副本会重复扫（trigger 幂等：insert_init ON CONFLICT + cas_transition
天然抗重复）但费 BKD QPS。若 replicaCount > 1，把 SISYPHUS_SNAPSHOT_INTERVAL_SEC=0 关掉
所有副本，另跑 K8s CronJob 调一次 sync_once()。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import structlog

from . import engine
from .bkd import BKDClient, Issue
from .config import settings
from .router import (
    _get_target,
    extract_req_id,
    get_parent_id,
    get_parent_stage,
    get_round,
)
from .state import Event
from .store import db, req_state


def _parse_iso(s: str | None) -> datetime | None:
    """BKD 返 ISO 字符串带 'Z' 后缀，asyncpg 期望 datetime 实例。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

log = structlog.get_logger(__name__)

_STAGE_FROM_TAGS = (
    "analyze",
    # M16 起新 issue 走 "spec"；保留 contract-spec/acceptance-spec 识别历史数据
    "spec", "contract-spec", "acceptance-spec",
    "dev", "ci",
    "accept", "bugfix", "diagnose",
    "done-archive", "github-incident",
)


def _flatten(issue: Issue) -> dict:
    """Issue → bkd_snapshot row dict（结构化解析 tag 列）。"""
    tags = issue.tags or []
    stage = next((s for s in _STAGE_FROM_TAGS if s in tags), None)
    return {
        "issue_id": issue.id,
        "req_id": extract_req_id(tags),
        "stage": stage,
        "status": issue.status_id,
        "title": issue.title,
        "tags": tags,
        "round": get_round(tags) or None,
        "target": _get_target(set(tags)),
        "parent_issue_id": get_parent_id(tags),
        "parent_stage": get_parent_stage(tags),
        "created_at": _parse_iso(issue.created_at),
        "bkd_updated_at": _parse_iso(issue.updated_at),
    }


_UPSERT_SQL = """
INSERT INTO bkd_snapshot
  (issue_id, req_id, stage, status, title, tags, round, target,
   parent_issue_id, parent_stage, created_at, bkd_updated_at, synced_at)
VALUES
  ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12, now())
ON CONFLICT (issue_id) DO UPDATE SET
  req_id          = EXCLUDED.req_id,
  stage           = EXCLUDED.stage,
  status          = EXCLUDED.status,
  title           = EXCLUDED.title,
  tags            = EXCLUDED.tags,
  round           = EXCLUDED.round,
  target          = EXCLUDED.target,
  parent_issue_id = EXCLUDED.parent_issue_id,
  parent_stage    = EXCLUDED.parent_stage,
  created_at      = COALESCE(EXCLUDED.created_at, bkd_snapshot.created_at),
  bkd_updated_at  = EXCLUDED.bkd_updated_at,
  synced_at       = now()
"""


# BKD status 表征"用户已收尾不要再激活"。orphan 恢复必须跳。
_BKD_TERMINAL_STATUSES = frozenset({"done", "cancelled"})


def _make_recovery_body(issue: Issue, project_id: str) -> Any:
    """合成 webhook 形 body 给 engine.step / actions 用。

    engine.step 只读 `getattr(body, "issueId", None)` 落 obs；start_analyze 读
    `body.projectId` / `body.issueId` / `body.title`。给齐 WebhookBody 主要字段
    避免后续 action 加新依赖时漏字段。
    """
    return SimpleNamespace(
        event="issue.updated",
        issueId=issue.id,
        issueNumber=issue.issue_number,
        projectId=project_id,
        title=issue.title,
        tags=list(issue.tags or []),
        executionId=None,
        finalStatus=None,
        changes=None,
        timestamp=None,
    )


async def _trigger_orphan_intent_analyze(
    main_pool, project_id: str, issues: list[Issue],
) -> int:
    """对每个 BKD issue：若是 intent:analyze 入口但 req_state 还没行 → 起 INTENT_ANALYZE。

    返回成功 trigger 的条数。

    幂等：
    - `req_state.insert_init` 用 ON CONFLICT DO NOTHING，并发 webhook 不会双插
    - `engine.step` 内部 `cas_transition(INIT, ANALYZING)` 失败时 skip，并发推进无副作用
    - `start_analyze` 跑成功后会给 BKD issue 加 `analyze` tag —— 下个 tick 这个 issue
      不再被识别为 orphan
    """
    triggered = 0
    for issue in issues:
        tags = issue.tags or []
        if "intent:analyze" not in tags:
            continue
        # 已经被 start_analyze rebrand 过，不是 orphan
        if "analyze" in tags:
            continue
        # 用户已 close → 不要起死回生
        if issue.status_id in _BKD_TERMINAL_STATUSES:
            continue
        # 解析 req_id：跟 webhook 保持一致——优先 REQ-* tag，fallback 到 issueNumber
        req_id = extract_req_id(tags, issue.issue_number)
        if req_id is None:
            continue
        existing = await req_state.get(main_pool, req_id)
        if existing is not None:
            # 已有 req_state 行（webhook 早进来 / 别的副本先恢复了）
            continue

        log.info(
            "snapshot.orphan_intent_analyze.detected",
            req_id=req_id, issue_id=issue.id, project_id=project_id,
        )
        try:
            await req_state.insert_init(
                main_pool, req_id, project_id,
                context={
                    "intent_issue_id": issue.id,
                    "intent_title": (issue.title or "").strip(),
                    "snapshot_recovered": True,
                },
            )
            row = await req_state.get(main_pool, req_id)
            if row is None:
                # insert_init 跑完还拿不到行 → DB 异常，下 tick 再试
                log.warning(
                    "snapshot.orphan_intent_analyze.row_missing_post_insert",
                    req_id=req_id, issue_id=issue.id,
                )
                continue
            body = _make_recovery_body(issue, project_id)
            await engine.step(
                main_pool,
                body=body,
                req_id=req_id,
                project_id=project_id,
                tags=list(tags),
                cur_state=row.state,
                ctx=row.context,
                event=Event.INTENT_ANALYZE,
            )
            triggered += 1
        except Exception as e:
            # 单个 issue 出错不能拖垮整轮 —— 下 tick 重试
            log.warning(
                "snapshot.orphan_intent_analyze.failed",
                req_id=req_id, issue_id=issue.id,
                project_id=project_id, error=str(e),
            )
    return triggered


async def sync_once() -> int:
    """跑一次同步。返回写入 bkd_snapshot 的行数（与 orch-noise-cleanup 合约保持）。

    多项目：扫 req_state 里所有 distinct project_id，逐个 list-issues。每个 project 内
    先跑 orphan 恢复（不依赖 obs pool），再跑 bkd_snapshot UPSERT（仅 obs pool 在时）。
    新接入的 project 第一个 webhook 进来后，下个周期就会被扫到。

    orphan 恢复条数走日志（snapshot.synced 里的 orphans_triggered），不参与返回值，
    避免破坏 ORCHN-S3 "全部 project 都被排除时短路返回 0" 的契约。
    """
    main_pool = db.get_pool()
    obs_pool = db.get_obs_pool()

    rows_proj = await main_pool.fetch("SELECT DISTINCT project_id FROM req_state")
    excluded = set(settings.snapshot_exclude_project_ids)
    project_ids = [r["project_id"] for r in rows_proj if r["project_id"] not in excluded]
    if not project_ids:
        log.info("snapshot.no_projects_yet")
        return 0

    snapshot_rows = 0
    orphans_triggered = 0

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        for project_id in project_ids:
            try:
                issues = await bkd.list_issues(project_id, limit=500)
            except Exception as e:
                log.warning("snapshot.list_failed", project_id=project_id, error=str(e))
                continue

            # ─── orphan intent:analyze 恢复（与 obs pool 无关）─────────────
            try:
                orphans_triggered += await _trigger_orphan_intent_analyze(
                    main_pool, project_id, issues,
                )
            except Exception as e:
                log.warning(
                    "snapshot.orphan_pass_failed",
                    project_id=project_id, error=str(e),
                )

            # ─── bkd_snapshot UPSERT（best-effort，仅 obs 配置时）──────────
            if obs_pool is None:
                continue
            rows = [_flatten(i) for i in issues]
            if not rows:
                continue
            try:
                async with obs_pool.acquire() as conn:
                    async with conn.transaction():
                        for r in rows:
                            await conn.execute(
                                _UPSERT_SQL,
                                r["issue_id"], r["req_id"], r["stage"], r["status"],
                                r["title"], r["tags"], r["round"], r["target"],
                                r["parent_issue_id"], r["parent_stage"],
                                r["created_at"], r["bkd_updated_at"],
                            )
                snapshot_rows += len(rows)
            except Exception as e:
                log.warning(
                    "snapshot.upsert_failed",
                    project_id=project_id, error=str(e),
                )

    log.info(
        "snapshot.synced",
        snapshot_rows=snapshot_rows,
        orphans_triggered=orphans_triggered,
        projects=project_ids,
    )
    return snapshot_rows


async def run_loop() -> None:
    """后台常驻 task。"""
    interval = settings.snapshot_interval_sec
    if interval <= 0:
        log.info("snapshot.disabled", reason="interval<=0")
        return

    # 先睡 30s 让服务起稳
    await asyncio.sleep(30)
    while True:
        try:
            await sync_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("snapshot.loop_iter_failed", error=str(e))
        await asyncio.sleep(interval)
