"""obs_schema.apply_obs_schema 烟测：幂等、失败不阻断。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from orchestrator import obs_schema
from orchestrator.store import db


class FakeObsPool:
    def __init__(self):
        self.calls: list[str] = []
        self.execute = AsyncMock(side_effect=self._exec)

    async def _exec(self, sql: str) -> None:
        self.calls.append(sql)


@pytest.fixture(autouse=True)
def reset_default_path(monkeypatch):
    """每测完恢复 _DEFAULT_SCHEMA_PATH，避免测间污染。"""
    original = obs_schema._DEFAULT_SCHEMA_PATH
    yield
    monkeypatch.setattr(obs_schema, "_DEFAULT_SCHEMA_PATH", original)


@pytest.mark.asyncio
async def test_apply_skip_when_no_pool(monkeypatch):
    monkeypatch.setattr(db, "get_obs_pool", lambda: None)
    result = await obs_schema.apply_obs_schema()
    assert result is True


@pytest.mark.asyncio
async def test_apply_skip_when_schema_file_missing(monkeypatch):
    monkeypatch.setattr(db, "get_obs_pool", lambda: FakeObsPool())
    monkeypatch.setattr(obs_schema, "_DEFAULT_SCHEMA_PATH", Path("/nonexistent/schema.sql"))
    result = await obs_schema.apply_obs_schema()
    assert result is True


@pytest.mark.asyncio
async def test_apply_skip_when_schema_file_empty(monkeypatch, tmp_path):
    empty_file = tmp_path / "schema.sql"
    empty_file.write_text("   \n  ")
    monkeypatch.setattr(db, "get_obs_pool", lambda: FakeObsPool())
    result = await obs_schema.apply_obs_schema(schema_path=empty_file)
    assert result is True


@pytest.mark.asyncio
async def test_apply_executes_schema_sql(monkeypatch, tmp_path):
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("CREATE TABLE IF NOT EXISTS t (id INT);")
    pool = FakeObsPool()
    monkeypatch.setattr(db, "get_obs_pool", lambda: pool)
    result = await obs_schema.apply_obs_schema(schema_path=schema_file)
    assert result is True
    assert len(pool.calls) == 1
    assert "CREATE TABLE IF NOT EXISTS t" in pool.calls[0]


@pytest.mark.asyncio
async def test_apply_swallows_exception(monkeypatch, tmp_path):
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("CREATE TABLE t (id INT);")
    pool = FakeObsPool()
    pool.execute.side_effect = RuntimeError("PG down")
    monkeypatch.setattr(db, "get_obs_pool", lambda: pool)
    result = await obs_schema.apply_obs_schema(schema_path=schema_file)
    assert result is False


def test_resolve_schema_path_env_override(monkeypatch, tmp_path):
    schema_file = tmp_path / "obs.sql"
    schema_file.write_text("SELECT 1;")
    monkeypatch.setenv("SISYPHUS_OBS_SCHEMA_PATH", str(schema_file))
    path = obs_schema._resolve_schema_path()
    assert path == schema_file


def test_resolve_schema_path_fallback_exists():
    """fallback 路径（repo 根/observability/schema.sql）在 CI 环境应存在。"""
    path = obs_schema._resolve_schema_path()
    assert path.is_file(), f"schema.sql not found at {path}"
    assert "CREATE TABLE IF NOT EXISTS event_log" in path.read_text()
