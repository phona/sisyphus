"""observability schema 自动应用。

启动时把 observability/schema.sql 刷到 obs DB。
幂等（全用 CREATE IF NOT EXISTS / OR REPLACE），失败不阻断主服务。
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog

from .store import db

log = structlog.get_logger(__name__)


def _resolve_schema_path() -> Path:
    """定位 observability/schema.sql。

    优先级：env > cwd 相对 > 包相对（dev editable 装时）。
    """
    env = os.environ.get("SISYPHUS_OBS_SCHEMA_PATH", "")
    if env:
        return Path(env)
    # dev 跑 pytest / uvicorn：cwd 在 orchestrator/，同级有 observability/
    cwd_schema = Path.cwd().parent / "observability" / "schema.sql"
    if cwd_schema.is_file():
        return cwd_schema
    # 兜底：从 src/orchestrator/obs_schema.py 往上 3 级到 repo 根
    return Path(__file__).resolve().parents[3] / "observability" / "schema.sql"


_DEFAULT_SCHEMA_PATH = _resolve_schema_path()


async def apply_obs_schema(
    schema_path: Path | None = None,
) -> bool:
    """把 schema.sql 刷到 obs pool；dsn 未配或文件不存在则跳过。

    返回 True = 执行了（或无需执行），False = 抛了异常。
    调用方应 catch 并 log warning，不阻断启动。
    """
    pool = db.get_obs_pool()
    if pool is None:
        log.info("obs_schema.skip_no_pool")
        return True

    path = schema_path or _DEFAULT_SCHEMA_PATH
    if not path.is_file():
        log.warning("obs_schema.not_found", path=str(path))
        return True

    sql = path.read_text()
    if not sql.strip():
        log.warning("obs_schema.empty", path=str(path))
        return True

    try:
        await pool.execute(sql)
        log.info("obs_schema.applied", path=str(path))
        return True
    except Exception as e:
        log.warning("obs_schema.failed", path=str(path), error=str(e))
        return False
