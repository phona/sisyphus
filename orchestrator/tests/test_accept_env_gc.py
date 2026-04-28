"""accept_env_gc.gc_once 单测：mock PG pool + k8s_runner controller。"""
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
    """注入 fake controller + 记录 namespace 操作。"""
    fake = MagicMock()
    fake.list_accept_env_namespaces = AsyncMock(return_value=[])
    fake.delete_namespace = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.mark.asyncio
async def test_active_req_keeps_namespace(monkeypatch, mock_controller):
    """非终态 REQ 的 accept namespace 保留。"""
    pool = _FakePool([
        _row("REQ-1", "accept-running"),
        _row("REQ-2", "analyzing"),
    ])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.list_accept_env_namespaces = AsyncMock(
        return_value=["accept-req-1", "accept-req-2"],
    )

    result = await accept_env_gc.gc_once()

    assert result["kept_namespaces"] == ["accept-req-1", "accept-req-2"]
    assert result["cleaned_namespaces"] == []
    mock_controller.delete_namespace.assert_not_awaited()


@pytest.mark.asyncio
async def test_done_req_cleans_namespace(monkeypatch, mock_controller):
    """done REQ 的 accept namespace 被删。"""
    pool = _FakePool([
        _row("REQ-1", "done"),
    ])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.list_accept_env_namespaces = AsyncMock(
        return_value=["accept-req-1"],
    )

    result = await accept_env_gc.gc_once()

    assert result["cleaned_namespaces"] == ["accept-req-1"]
    assert result["kept_namespaces"] == []
    mock_controller.delete_namespace.assert_awaited_once_with("accept-req-1")


@pytest.mark.asyncio
async def test_escalated_req_cleans_namespace(monkeypatch, mock_controller):
    """escalated REQ 的 accept namespace 被删（跟 runner PVC retention 不同，
    accept env 没有 retention 概念——namespace 只占用 cluster 资源，不给人 debug）。"""
    pool = _FakePool([
        _row("REQ-1", "escalated"),
    ])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.list_accept_env_namespaces = AsyncMock(
        return_value=["accept-req-1"],
    )

    result = await accept_env_gc.gc_once()

    assert result["cleaned_namespaces"] == ["accept-req-1"]
    mock_controller.delete_namespace.assert_awaited_once_with("accept-req-1")


@pytest.mark.asyncio
async def test_orphan_namespace_cleaned(monkeypatch, mock_controller):
    """req_state 中找不到的 REQ（orphan）的 namespace 也清。"""
    pool = _FakePool([
        _row("REQ-1", "analyzing"),
    ])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.list_accept_env_namespaces = AsyncMock(
        return_value=["accept-req-1", "accept-req-orphan"],
    )

    result = await accept_env_gc.gc_once()

    assert result["kept_namespaces"] == ["accept-req-1"]
    assert result["cleaned_namespaces"] == ["accept-req-orphan"]
    mock_controller.delete_namespace.assert_awaited_once_with("accept-req-orphan")


@pytest.mark.asyncio
async def test_no_namespaces_nothing_done(monkeypatch, mock_controller):
    """cluster 里没有 accept namespace → 空扫。"""
    pool = _FakePool([
        _row("REQ-1", "done"),
    ])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.list_accept_env_namespaces = AsyncMock(return_value=[])

    result = await accept_env_gc.gc_once()

    assert result["cleaned_namespaces"] == []
    assert result["kept_namespaces"] == []
    mock_controller.delete_namespace.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_no_controller(monkeypatch):
    """没 K8s controller 时安全跳过（返 {skipped: ...}）。"""
    k8s_runner.set_controller(None)
    result = await accept_env_gc.gc_once()
    assert "skipped" in result


@pytest.mark.asyncio
async def test_gc_once_updates_last_result_with_ran_at(monkeypatch, mock_controller):
    """gc_once 正常执行后 _last_gc_result 含 ran_at。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.list_accept_env_namespaces = AsyncMock(return_value=[])

    await accept_env_gc.gc_once()

    last = accept_env_gc.get_last_result()
    assert last is not None
    assert "ran_at" in last
    assert "cleaned_namespaces" in last
    assert "kept_namespaces" in last


@pytest.mark.asyncio
async def test_gc_once_skipped_also_updates_last_result():
    """no controller 时 skipped 结果也更新 _last_gc_result（含 ran_at）。"""
    k8s_runner.set_controller(None)

    await accept_env_gc.gc_once()

    last = accept_env_gc.get_last_result()
    assert last is not None
    assert "skipped" in last
    assert "ran_at" in last


@pytest.mark.asyncio
async def test_delete_404_counts_as_cleaned(monkeypatch, mock_controller):
    """namespace 已被别处删了（404）→ 算清理成功，不抛异常。"""
    from kubernetes.client import ApiException

    pool = _FakePool([_row("REQ-1", "done")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.list_accept_env_namespaces = AsyncMock(
        return_value=["accept-req-1"],
    )
    mock_controller.delete_namespace = AsyncMock(
        side_effect=ApiException(status=404, reason="Not Found"),
    )

    result = await accept_env_gc.gc_once()

    assert result["cleaned_namespaces"] == ["accept-req-1"]
