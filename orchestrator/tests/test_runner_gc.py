"""runner_gc.gc_once / gc_pvcs 单测：mock PG pool + k8s_runner controller。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import k8s_runner, runner_gc


class _FakePool:
    def __init__(self, rows, terminal_rows=None):
        self._rows = rows
        self._terminal_rows = terminal_rows or []

    async def fetch(self, sql, *args):
        # 区分不同的 SQL 查询
        if "state IN ('done', 'escalated')" in sql:
            return self._terminal_rows
        if "state <> ALL" in sql:
            # 返回非 terminal rows（空列表，表示没有非 terminal）
            return []
        return self._rows


def _row(req_id, state, updated_at=None):
    return {
        "req_id": req_id, "state": state,
        "updated_at": updated_at, "context": {},
    }


@pytest.fixture
def mock_controller(monkeypatch):
    """注入 fake controller + 记录 gc_orphans 被调用的 keep_set。"""
    fake = MagicMock()
    fake.gc_orphans = AsyncMock(return_value=[])
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.mark.asyncio
async def test_active_includes_inflight(monkeypatch, mock_controller):
    """in-flight 状态（非 done/escalated）全部算 active，runner 保留。"""
    pool = _FakePool([
        _row("REQ-1", "analyzing"),
        _row("REQ-2", "staging-test-running"),
        _row("REQ-3", "accept-running"),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()

    called_with = mock_controller.gc_orphans.await_args.args[0]
    assert called_with == {"REQ-1", "REQ-2", "REQ-3"}


@pytest.mark.asyncio
async def test_done_state_not_active(monkeypatch, mock_controller):
    """done 的 REQ 立即移出 active（runner 会被清）。"""
    pool = _FakePool([
        _row("REQ-1", "done", updated_at=datetime.now(UTC)),
        _row("REQ-2", "analyzing"),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()
    called_with = mock_controller.gc_orphans.await_args.args[0]
    assert called_with == {"REQ-2"}   # done 不在 keep 集合


@pytest.mark.asyncio
async def test_escalated_within_retention_kept(monkeypatch, mock_controller):
    """escalated 但还在保留期内（默认 7 天）→ 仍 active（PVC 留给人翻）。"""
    recent = datetime.now(UTC) - timedelta(days=1)
    pool = _FakePool([
        _row("REQ-1", "escalated", updated_at=recent),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()
    called_with = mock_controller.gc_orphans.await_args.args[0]
    assert called_with == {"REQ-1"}


@pytest.mark.asyncio
async def test_escalated_past_retention_cleaned(monkeypatch, mock_controller):
    """escalated 超过保留期 → 移出 active，runner 会被清。"""
    old = datetime.now(UTC) - timedelta(days=30)
    pool = _FakePool([
        _row("REQ-1", "escalated", updated_at=old),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()
    called_with = mock_controller.gc_orphans.await_args.args[0]
    assert called_with == set()   # 空 keep = REQ-1 被 gc_orphans 清


@pytest.mark.asyncio
async def test_skips_when_no_controller(monkeypatch):
    """没 K8s controller 时安全跳过（返 {skipped: ...}）。"""
    k8s_runner.set_controller(None)
    result = await runner_gc.gc_once()
    assert "skipped" in result


# ─── gc_pvcs 单测 ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_controller_with_pvc(monkeypatch):
    """注入 fake controller，记录 delete_pvc 调用。"""
    fake = MagicMock()
    fake.delete_pvc = AsyncMock(return_value=True)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.mark.asyncio
async def test_runner_gc_pvc_done_immediate(monkeypatch, mock_controller_with_pvc):
    """state=done 的 PVC 立即被 GC 调用 delete_pvc。"""
    now = datetime.now(UTC)
    terminal_rows = [_row("REQ-D1", "done", updated_at=now)]
    pool = _FakePool(rows=[], terminal_rows=terminal_rows)
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    monkeypatch.setattr("orchestrator.runner_gc._disk_pressure", AsyncMock(return_value=0.5))

    result = await runner_gc.gc_pvcs()

    mock_controller_with_pvc.delete_pvc.assert_called_once_with("REQ-D1")
    assert "REQ-D1" in result["pvc_deleted"]


@pytest.mark.asyncio
async def test_runner_gc_pvc_escalated_under_24h_kept(monkeypatch, mock_controller_with_pvc):
    """state=escalated 但 < 24h → 不删 PVC。"""
    recent = datetime.now(UTC) - timedelta(hours=12)
    terminal_rows = [_row("REQ-E1", "escalated", updated_at=recent)]
    pool = _FakePool(rows=[], terminal_rows=terminal_rows)
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    monkeypatch.setattr("orchestrator.runner_gc._disk_pressure", AsyncMock(return_value=0.5))

    result = await runner_gc.gc_pvcs()

    mock_controller_with_pvc.delete_pvc.assert_not_called()
    assert result["pvc_deleted"] == []


@pytest.mark.asyncio
async def test_runner_gc_pvc_escalated_over_24h_deleted(monkeypatch, mock_controller_with_pvc):
    """state=escalated 超 24h → 删 PVC。"""
    old = datetime.now(UTC) - timedelta(hours=25)
    terminal_rows = [_row("REQ-E2", "escalated", updated_at=old)]
    pool = _FakePool(rows=[], terminal_rows=terminal_rows)
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    monkeypatch.setattr("orchestrator.runner_gc._disk_pressure", AsyncMock(return_value=0.5))

    result = await runner_gc.gc_pvcs()

    mock_controller_with_pvc.delete_pvc.assert_called_once_with("REQ-E2")
    assert "REQ-E2" in result["pvc_deleted"]


@pytest.mark.asyncio
async def test_runner_gc_pvc_disk_pressure_purge(monkeypatch, mock_controller_with_pvc):
    """disk pressure > 80% → 强清 non-active PVC（含未过期 escalated）。"""
    recent = datetime.now(UTC) - timedelta(hours=1)
    terminal_rows = [_row("REQ-P1", "escalated", updated_at=recent)]
    pool = _FakePool(rows=[], terminal_rows=terminal_rows)
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    # 模拟 disk > 80%
    monkeypatch.setattr("orchestrator.runner_gc._disk_pressure", AsyncMock(return_value=0.85))

    result = await runner_gc.gc_pvcs()

    mock_controller_with_pvc.delete_pvc.assert_called_once_with("REQ-P1")
    assert "REQ-P1" in result["pvc_deleted"]


@pytest.mark.asyncio
async def test_runner_gc_pvc_skips_active_on_disk_pressure(monkeypatch, mock_controller_with_pvc):
    """disk pressure > 80% 时 active REQ 的 PVC 不删。"""
    recent = datetime.now(UTC) - timedelta(hours=1)
    terminal_rows = [_row("REQ-A1", "escalated", updated_at=recent)]

    class _ActivePool:
        async def fetch(self, sql, *args):
            if "state IN ('done', 'escalated')" in sql:
                return terminal_rows
            if "state <> ALL" in sql:
                # REQ-A1 is active (in-flight)
                return [{"req_id": "REQ-A1"}]
            return []

    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: _ActivePool())
    monkeypatch.setattr("orchestrator.runner_gc._disk_pressure", AsyncMock(return_value=0.9))

    result = await runner_gc.gc_pvcs()

    # REQ-A1 is active → not deleted despite disk pressure
    mock_controller_with_pvc.delete_pvc.assert_not_called()
    assert result["pvc_deleted"] == []
