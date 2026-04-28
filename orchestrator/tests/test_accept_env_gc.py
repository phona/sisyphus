"""accept_env_gc 单测：mock PG pool + k8s_runner controller。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import accept_env_gc, k8s_runner


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args):
        return self._rows


def _row(req_id, state):
    return {"req_id": req_id, "state": state}


@pytest.fixture(autouse=True)
def _reset_last_gc_result():
    """每个 test 前后重置 _last_gc_result，防止状态泄漏。"""
    accept_env_gc._last_gc_result = None
    yield
    accept_env_gc._last_gc_result = None


@pytest.fixture
def mock_controller(monkeypatch):
    """注入 fake controller + 记录 gc_accept_env_namespaces 的 keep_set。"""
    fake = MagicMock()
    fake.gc_accept_env_namespaces = AsyncMock(return_value=[])
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.mark.asyncio
async def test_active_inflight_kept(monkeypatch, mock_controller):
    """in-flight 状态（非 done/escalated）全部算 active，namespace 保留。"""
    pool = _FakePool([
        _row("REQ-1", "analyzing"),
        _row("REQ-2", "staging-test-running"),
        _row("REQ-3", "accept-running"),
    ])
    monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: pool)

    result = await accept_env_gc.gc_once()

    keep = mock_controller.gc_accept_env_namespaces.await_args.args[0]
    assert keep == {"REQ-1", "REQ-2", "REQ-3"}
    assert result["cleaned_namespaces"] == []
    assert result["keep_count"] == 3


@pytest.mark.asyncio
async def test_terminal_state_cleaned(monkeypatch, mock_controller):
    """done / escalated 的 REQ 移出 keep set，对应 namespace 会被清理。"""
    pool = _FakePool([
        _row("REQ-1", "done"),
        _row("REQ-2", "escalated"),
        _row("REQ-3", "analyzing"),
    ])
    monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: pool)
    mock_controller.gc_accept_env_namespaces = AsyncMock(
        return_value=["accept-req-1", "accept-req-2"]
    )

    result = await accept_env_gc.gc_once()

    keep = mock_controller.gc_accept_env_namespaces.await_args.args[0]
    assert keep == {"REQ-3"}
    assert result["cleaned_namespaces"] == ["accept-req-1", "accept-req-2"]
    assert result["keep_count"] == 1


@pytest.mark.asyncio
async def test_skips_when_no_controller(monkeypatch):
    """没 K8s controller 时安全跳过（返 {skipped: ...}）。"""
    k8s_runner.set_controller(None)
    result = await accept_env_gc.gc_once()
    assert "skipped" in result


@pytest.mark.asyncio
async def test_gc_once_returns_ran_at(monkeypatch, mock_controller):
    """gc_once 正常执行后返回 dict 含 ran_at。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: pool)

    result = await accept_env_gc.gc_once()

    assert "ran_at" in result
    assert "cleaned_namespaces" in result
    assert "keep_count" in result


@pytest.mark.asyncio
async def test_gc_once_skipped_also_updates_last_result():
    """no controller 时 skipped 结果也更新 _last_gc_result（含 ran_at）。"""
    k8s_runner.set_controller(None)

    await accept_env_gc.gc_once()

    last = accept_env_gc.get_last_result()
    assert last is not None
    assert "skipped" in last
    assert "ran_at" in last


def test_get_last_result_returns_none_before_any_gc():
    """首次 GC 前 get_last_result() 返 None。"""
    assert accept_env_gc.get_last_result() is None


@pytest.mark.asyncio
async def test_gc_once_updates_last_result(monkeypatch, mock_controller):
    """gc_once 正常执行后 _last_gc_result 含 ran_at。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: pool)

    await accept_env_gc.gc_once()

    last = accept_env_gc.get_last_result()
    assert last is not None
    assert "ran_at" in last
    assert "cleaned_namespaces" in last
    assert "keep_count" in last
