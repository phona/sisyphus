"""runner_gc.gc_once 单测：mock PG pool + k8s_runner controller。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes.client import ApiException

from orchestrator import k8s_runner, runner_gc


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args):
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


@pytest.fixture(autouse=True)
def _reset_disk_check_flag():
    """每个 case 前后重置 _DISK_CHECK_DISABLED，防止前一 test 把它置为 True。"""
    runner_gc._DISK_CHECK_DISABLED = False
    yield
    runner_gc._DISK_CHECK_DISABLED = False


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
async def test_escalated_within_retention_purged_on_disk_pressure(monkeypatch, mock_controller):
    """disk > threshold → escalated 也强清（不留 retention）。"""
    from unittest.mock import AsyncMock
    recent = datetime.now(UTC) - timedelta(hours=2)
    pool = _FakePool([
        _row("REQ-1", "escalated", updated_at=recent),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    # 模拟磁盘 90% 用了
    mock_controller.node_disk_usage_ratio = AsyncMock(return_value=0.9)

    result = await runner_gc.gc_once()
    assert result["disk_pressure"] is True
    called_with = mock_controller.gc_orphans.await_args.args[0]
    assert called_with == set()  # 紧急模式，escalated 不再 keep


@pytest.mark.asyncio
async def test_escalated_within_retention_kept(monkeypatch, mock_controller):
    """escalated 但还在保留期内（默认 1 天）→ 仍 active（PVC 留给人 PR #48 resume）。"""
    recent = datetime.now(UTC) - timedelta(hours=2)  # < 1 day default retention
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


@pytest.mark.asyncio
async def test_disk_check_403_disables_after_first_log(monkeypatch, mock_controller, capsys):
    """ApiException(403) → 进程级 flag 置 True；INFO log 一次，disk_pressure=False。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    mock_controller.node_disk_usage_ratio = AsyncMock(
        side_effect=ApiException(status=403, reason="Forbidden"),
    )

    assert runner_gc._DISK_CHECK_DISABLED is False
    result = await runner_gc.gc_once()

    assert result["disk_pressure"] is False
    assert runner_gc._DISK_CHECK_DISABLED is True
    out = capsys.readouterr().out
    assert "disk_check_rbac_denied" in out


@pytest.mark.asyncio
async def test_disk_check_short_circuits_after_disabled(monkeypatch, mock_controller):
    """_DISK_CHECK_DISABLED=True 时不再调 node_disk_usage_ratio。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    mock_controller.node_disk_usage_ratio = AsyncMock(return_value=0.5)
    runner_gc._DISK_CHECK_DISABLED = True

    result = await runner_gc.gc_once()

    assert result["disk_pressure"] is False
    mock_controller.node_disk_usage_ratio.assert_not_awaited()


@pytest.mark.asyncio
async def test_disk_check_non_403_keeps_probe_alive(monkeypatch, mock_controller):
    """ApiException(500) → 不禁用，下一轮还会再尝试。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    mock_controller.node_disk_usage_ratio = AsyncMock(
        side_effect=ApiException(status=500, reason="Internal"),
    )

    result = await runner_gc.gc_once()

    assert result["disk_pressure"] is False
    assert runner_gc._DISK_CHECK_DISABLED is False  # 没禁用
