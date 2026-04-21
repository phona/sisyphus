"""yoyo-migrations 包装。

约定：项目根 `migrations/` 下顺序命名的 SQL 文件
（`0001_*.sql`、`0002_*.sql`...）。yoyo 自动在 `_yoyo_migration` 表
记录已 apply 的版本，启动时只跑差量。

每个 SQL 文件可写：
    -- forward (默认整段视为 forward)
    CREATE TABLE ...;

    -- !rollback
    DROP TABLE ...;
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog
from yoyo import get_backend, read_migrations

log = structlog.get_logger(__name__)


def _resolve_default_dir() -> Path:
    """找 migrations 目录。优先级：env > cwd/migrations > 包相对（dev 模式）。

    - 容器：Dockerfile 设 SISYPHUS_MIGRATIONS_DIR=/app/migrations
    - dev 跑 pytest / uvicorn：cwd 在 orchestrator/，cwd/migrations 命中
    - 兜底：从 src/orchestrator/migrate.py 往上 2 级（dev editable 装时的 repo 根）
    """
    env = os.environ.get("SISYPHUS_MIGRATIONS_DIR", "")
    if env:
        return Path(env)
    cwd_mig = Path.cwd() / "migrations"
    if cwd_mig.is_dir():
        return cwd_mig
    return Path(__file__).resolve().parents[2] / "migrations"


_DEFAULT_MIGRATIONS_DIR = _resolve_default_dir()


def apply_pending(dsn: str, migrations_dir: Path | None = None) -> int:
    """同步跑所有未 apply 的迁移。返回新 apply 的迁移数。

    yoyo 用 psycopg2 同步 driver；因为只在启动时跑一次，没必要 async。
    DSN 必须是 yoyo 支持的形式，`postgresql://...` 直接可用。
    """
    path = migrations_dir or _DEFAULT_MIGRATIONS_DIR
    if not path.is_dir():
        log.warning("migrate.no_dir", path=str(path))
        return 0

    backend = get_backend(dsn)
    with backend.lock():
        migrations = backend.to_apply(read_migrations(str(path)))
        if not migrations:
            log.info("migrate.up_to_date", dir=str(path))
            return 0
        ids = [m.id for m in migrations]
        log.info("migrate.applying", count=len(migrations), ids=ids)
        backend.apply_migrations(migrations)
        log.info("migrate.done", count=len(migrations))
        return len(migrations)


# yoyo 默认 `postgresql://` scheme 即用 psycopg2 driver（已通过 psycopg2-binary 提供）
# 不做 DSN 改写 — asyncpg 和 yoyo 用同一份 dsn 即可
