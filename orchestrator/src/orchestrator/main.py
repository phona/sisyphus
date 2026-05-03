"""FastAPI 入口。"""
from __future__ import annotations

import asyncio
import logging

import httpx
import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import (
    accept_env_gc,
    agent_turns_collector,
    k8s_runner,
    pr_health,
    runner_gc,
    snapshot,
    watchdog,
)
from .admin import admin as admin_api
from .config import settings
from .config_version import maybe_record_config_change
from .maintenance import table_ttl
from .migrate import apply_pending
from .obs_schema import apply_obs_schema
from .store import db
from .webhook import api as webhook_api


def _configure_logging() -> None:
    logging.basicConfig(level=settings.log_level)
    if settings.log_json:
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
        )


_configure_logging()
log = structlog.get_logger(__name__)
app = FastAPI(title="sisyphus-orchestrator", version="0.1.0")
app.include_router(webhook_api)
app.include_router(admin_api)

# 后台 task 句柄（shutdown 时取消）
_bg_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def startup() -> None:
    # 1. 跑 schema 迁移（同步，启动时一次性）
    #    helm init-container 负责时跳过，避免与 init-container 重复竞争锁
    if not settings.skip_migration_on_startup:
        apply_pending(settings.pg_dsn, lock_timeout=settings.migration_lock_timeout)
    # 2. 起业务 pool + observability pool（obs DSN 空就跳过）
    await db.init_pool(settings.pg_dsn)
    await db.init_obs_pool(settings.obs_pg_dsn)
    # 2b. 自动 apply observability schema（幂等，失败不阻断）
    await apply_obs_schema()
    # 2c. P0-2：检测 prompt/checker/config 变更，有则写 config_version 行（best-effort）
    obs_pool = db.get_obs_pool()
    if obs_pool is not None and settings.config_version_startup_hook_enabled:
        try:
            await maybe_record_config_change(obs_pool)
        except Exception as e:
            log.warning("startup.config_version.failed", error=str(e))
    # 3. 起 snapshot 后台同步 task（interval 0 不起）。
    # 这个 loop 现在两件事：obs UPSERT（依赖 obs pool） + intent:analyze orphan 恢复
    # （只依赖 main pool）。后者跟 obs DSN 无关，所以 startup gate 不再 require obs_pg_dsn。
    if settings.snapshot_interval_sec > 0:
        _bg_tasks.append(asyncio.create_task(snapshot.run_loop(), name="snapshot"))
    # 4. 初始化 K8s runner controller（v0.2 actions 调用）
    try:
        controller = k8s_runner.RunnerController(
            namespace=settings.runner_namespace,
            runner_image=settings.runner_image,
            runner_sa=settings.runner_service_account,
            storage_class=settings.runner_storage_class,
            workspace_size=settings.runner_workspace_size,
            runner_secret_name=settings.runner_secret_name,
            image_pull_secrets=settings.runner_image_pull_secrets,
            ready_timeout_sec=settings.runner_ready_timeout_sec,
            ready_attempts=settings.runner_ready_attempts,
            in_cluster=settings.k8s_in_cluster,
            kvm_enabled=settings.runner_kvm_enabled,
        )
        k8s_runner.set_controller(controller)
        log.info("k8s_runner.initialized", namespace=settings.runner_namespace)
        # 5. 起 runner GC 后台任务（周期清 done/escalated 过保留期的 PVC）
        if settings.runner_gc_interval_sec > 0:
            _bg_tasks.append(asyncio.create_task(runner_gc.run_loop(), name="runner_gc"))
        # 5b. 起 accept env GC 后台任务（清 terminal / orphan 的 accept-* namespace）
        if settings.accept_env_gc_interval_sec > 0:
            _bg_tasks.append(asyncio.create_task(accept_env_gc.run_loop(), name="accept_env_gc"))
    except Exception as e:
        # dev / 单机 / 没 kubeconfig 的场景允许失败（调 action 会抛，但 http 主进程能起）
        log.warning("k8s_runner.init_failed", error=str(e))
    # 6. 起 watchdog 兜底任务（M8：BKD 不发 session.failed 时周期性 escalate 卡死 REQ）
    if settings.watchdog_enabled and settings.watchdog_interval_sec > 0:
        _bg_tasks.append(asyncio.create_task(watchdog.run_loop(), name="watchdog"))
    # 7. 起 TTL 清理后台任务（event_seen / dispatch_slugs / verifier_decisions / stage_runs）
    if settings.ttl_cleanup_enabled and settings.ttl_cleanup_interval_sec > 0:
        _bg_tasks.append(asyncio.create_task(table_ttl.run_loop(), name="table_ttl"))
    # 8. 起 PR drift cron（REQ-fix-pr-queue-health-monitoring-1777789759）
    if settings.pr_health_enabled and settings.pr_health_interval_sec > 0:
        _bg_tasks.append(asyncio.create_task(pr_health.run_loop(), name="pr_health"))
    # 9. 起 agent turns collector（REQ-feat-agent-turns-collector-1777796671）
    if settings.agent_turns_collector_enabled and settings.agent_turns_collector_interval_sec > 0:
        _bg_tasks.append(
            asyncio.create_task(agent_turns_collector.run_loop(), name="agent_turns_collector")
        )
    log.info(
        "startup.ok",
        port=settings.port,
        obs_enabled=bool(settings.obs_pg_dsn),
        snapshot_interval=settings.snapshot_interval_sec,
        watchdog_enabled=settings.watchdog_enabled,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    for t in _bg_tasks:
        t.cancel()
    for t in _bg_tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    await db.close_pool()
    log.info("shutdown.ok")


@app.get("/livez")
async def livez() -> dict:
    """Liveness probe — 进程存活即 ok，不探依赖。"""
    return {"status": "ok"}


@app.get("/healthz")
async def healthz() -> dict:
    # deprecated: alias for /livez; readiness checks moved to /readyz
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness probe — 探 DB / BKD / K8s，任一失败返 503。"""
    failed: list[str] = []

    # DB ping (1s timeout)
    try:
        pool = db.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1", timeout=1.0)
    except Exception:
        failed.append("db")

    # BKD ping (2s timeout)
    try:
        async with httpx.AsyncClient(timeout=2.0) as hc:
            r = await hc.get(settings.bkd_base_url)
        if r.status_code >= 500:
            failed.append("bkd")
    except Exception:
        failed.append("bkd")

    # K8s ping (2s timeout; skip if controller not initialized — dev mode).
    # 探 namespaced pod list 而不是 list_namespace —— 后者要 cluster-wide
    # RBAC，chart 只装了 namespace-scoped Role（issue #344）。
    try:
        controller = k8s_runner.get_controller()
        await asyncio.to_thread(
            controller.core_v1.list_namespaced_pod,
            controller.namespace,
            limit=1,
            _request_timeout=2,
        )
    except RuntimeError:
        pass  # controller not initialized in dev/test mode
    except Exception:
        failed.append("k8s")

    if failed:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "failed": failed},
        )
    return {"status": "ok"}
