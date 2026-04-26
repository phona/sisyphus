"""FastAPI 入口。"""
from __future__ import annotations

import asyncio
import logging

import structlog
from fastapi import FastAPI

from . import k8s_runner, runner_gc, snapshot, watchdog
from .admin import admin as admin_api
from .config import settings
from .migrate import apply_pending
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
    apply_pending(settings.pg_dsn)
    # 2. 起业务 pool + observability pool（obs DSN 空就跳过）
    await db.init_pool(settings.pg_dsn)
    await db.init_obs_pool(settings.obs_pg_dsn)
    # 3. 起 bkd_snapshot 后台同步 task（interval 0 不起）
    if settings.snapshot_interval_sec > 0 and settings.obs_pg_dsn:
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
    except Exception as e:
        # dev / 单机 / 没 kubeconfig 的场景允许失败（调 action 会抛，但 http 主进程能起）
        log.warning("k8s_runner.init_failed", error=str(e))
    # 6. 起 watchdog 兜底任务（M8：BKD 不发 session.failed 时周期性 escalate 卡死 REQ）
    if settings.watchdog_enabled and settings.watchdog_interval_sec > 0:
        _bg_tasks.append(asyncio.create_task(watchdog.run_loop(), name="watchdog"))
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


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
