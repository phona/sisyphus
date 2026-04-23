"""bkd_snapshot 同步：定时拉 BKD list-issues，UPSERT 到 sisyphus_obs.bkd_snapshot。

替代旧 n8n schedule workflow（每 5 min cron）。

后台 asyncio task，main.py startup 时启动。
观测系数据，best-effort：失败只 log 不挂主流程。

多副本注意：N>1 副本会重复拉 BKD（UPSERT 幂等无害但费 BKD QPS）。
若部署时 replicaCount > 1，可把 SISYPHUS_SNAPSHOT_INTERVAL_SEC=0 关掉所有副本，
另跑 K8s CronJob 调一次 sync_once()。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import structlog

from .bkd import BKDClient, Issue
from .config import settings
from .router import _get_target, extract_req_id, get_parent_id, get_parent_stage, get_round
from .store import db


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


async def sync_once() -> int:
    """跑一次同步。返回写入行数。无 obs DB 配置返 0。

    多项目：扫 req_state 里所有 distinct project_id，逐个 list-issues + UPSERT。
    新接入的 project 第一个 webhook 进来后，下个周期就会被扫到。
    """
    obs_pool = db.get_obs_pool()
    if obs_pool is None:
        return 0
    main_pool = db.get_pool()

    rows_proj = await main_pool.fetch("SELECT DISTINCT project_id FROM req_state")
    project_ids = [r["project_id"] for r in rows_proj]
    if not project_ids:
        log.info("snapshot.no_projects_yet")
        return 0

    total = 0
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        for project_id in project_ids:
            try:
                issues = await bkd.list_issues(project_id, limit=500)
            except Exception as e:
                log.warning("snapshot.list_failed", project_id=project_id, error=str(e))
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
                total += len(rows)
            except Exception as e:
                log.warning("snapshot.upsert_failed", project_id=project_id, error=str(e))

    log.info("snapshot.synced", total=total, projects=project_ids)
    return total


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
