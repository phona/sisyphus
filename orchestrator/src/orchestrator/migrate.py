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

from pathlib import Path

import structlog
from yoyo import get_backend, read_migrations

log = structlog.get_logger(__name__)

# repo 根的 migrations/ 目录（src/orchestrator/migrate.py → ../../migrations）
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def apply_pending(dsn: str, migrations_dir: Path | None = None) -> int:
    """同步跑所有未 apply 的迁移。返回新 apply 的迁移数。

    yoyo 用 psycopg2 同步 driver；因为只在启动时跑一次，没必要 async。
    DSN 必须是 yoyo 支持的形式，`postgresql://...` 直接可用。
    """
    path = migrations_dir or _DEFAULT_MIGRATIONS_DIR
    if not path.is_dir():
        log.warning("migrate.no_dir", path=str(path))
        return 0

    backend = get_backend(_to_yoyo_dsn(dsn))
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


def _to_yoyo_dsn(dsn: str) -> str:
    """asyncpg 用 `postgresql://` / `postgres://`，yoyo 默认走 psycopg2 也吃。"""
    # yoyo 把 `postgres://` 当成 psycopg2，`postgresql+psycopg://` 走 psycopg3。
    # 我们装的 psycopg2-binary，强制成 psycopg2 scheme。
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg2://" + dsn.removeprefix("postgresql://")
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg2://" + dsn.removeprefix("postgres://")
    return dsn
