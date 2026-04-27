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
    """注入 fake controller + 记录两个 sweep 的 keep_set。"""
    fake = MagicMock()
    fake.gc_orphan_pods = AsyncMock(return_value=[])
    fake.gc_orphan_pvcs = AsyncMock(return_value=[])
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
    """in-flight 状态（非 done/escalated）全部算 active，runner 保留（pod + pvc）。"""
    pool = _FakePool([
        _row("REQ-1", "analyzing"),
        _row("REQ-2", "staging-test-running"),
        _row("REQ-3", "accept-running"),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()

    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]
    assert pod_keep == {"REQ-1", "REQ-2", "REQ-3"}
    assert pvc_keep == {"REQ-1", "REQ-2", "REQ-3"}


@pytest.mark.asyncio
async def test_done_state_not_active(monkeypatch, mock_controller):
    """done 的 REQ 立即移出 pod + pvc keep（runner 会被两个 sweep 都清）。"""
    pool = _FakePool([
        _row("REQ-1", "done", updated_at=datetime.now(UTC)),
        _row("REQ-2", "analyzing"),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()
    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]
    assert pod_keep == {"REQ-2"}
    assert pvc_keep == {"REQ-2"}


@pytest.mark.asyncio
async def test_pod_keep_excludes_escalated_within_retention(monkeypatch, mock_controller):
    """escalated REQ 在 retention 内：PVC 保留给人 debug，但 Pod 立即可清。

    REQ-runner-gc-pod-pvc-split 的核心：拆开 pod / pvc 的保留语义。
    Pod 占 512Mi 内存白白吃调度容量；PVC 才是给人 kubectl exec 看现场的。
    """
    recent = datetime.now(UTC) - timedelta(hours=2)  # < 1 day default retention
    pool = _FakePool([
        _row("REQ-1", "escalated", updated_at=recent),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()
    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]
    assert pod_keep == set()        # Pod 立即可清（哪怕 retention 内）
    assert pvc_keep == {"REQ-1"}    # PVC 留 retention 给人 debug


@pytest.mark.asyncio
async def test_escalated_within_retention_purged_on_disk_pressure(monkeypatch, mock_controller):
    """disk > threshold → escalated PVC 也强清（不留 retention）。Pod 永远不留。"""
    recent = datetime.now(UTC) - timedelta(hours=2)
    pool = _FakePool([
        _row("REQ-1", "escalated", updated_at=recent),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    # 模拟磁盘 90% 用了
    mock_controller.node_disk_usage_ratio = AsyncMock(return_value=0.9)

    result = await runner_gc.gc_once()
    assert result["disk_pressure"] is True
    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]
    assert pod_keep == set()  # 跟 disk pressure 无关：Pod keep 永远不含 terminal
    assert pvc_keep == set()  # 紧急模式：escalated PVC 也不再 keep


@pytest.mark.asyncio
async def test_escalated_past_retention_cleaned(monkeypatch, mock_controller):
    """escalated 超过保留期 → 移出 pvc keep（runner 会被清）。"""
    old = datetime.now(UTC) - timedelta(days=30)
    pool = _FakePool([
        _row("REQ-1", "escalated", updated_at=old),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()
    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]
    assert pod_keep == set()
    assert pvc_keep == set()


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


@pytest.mark.asyncio
async def test_gc_once_returns_split_cleaned_lists(monkeypatch, mock_controller):
    """gc_once 返 dict 必须含 cleaned_pods + cleaned_pvcs（分开记录）。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    mock_controller.gc_orphan_pods = AsyncMock(return_value=["REQ-zombie-pod"])
    mock_controller.gc_orphan_pvcs = AsyncMock(return_value=["REQ-zombie-pvc"])

    result = await runner_gc.gc_once()

    assert result["cleaned_pods"] == ["REQ-zombie-pod"]
    assert result["cleaned_pvcs"] == ["REQ-zombie-pvc"]
    assert result["pod_kept"] == 1
    assert result["pvc_kept"] == 1


# ═══════════════════════════════════════════════════════════════════════
# _last_gc_result tracking (REQ-430)
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_last_gc_result():
    """每个 test 前后重置 _last_gc_result，防止状态泄漏。"""
    runner_gc._last_gc_result = None
    yield
    runner_gc._last_gc_result = None


def test_get_last_result_returns_none_before_any_gc():
    """RGA-S4 precondition: 首次 GC 前 get_last_result() 返 None。"""
    assert runner_gc.get_last_result() is None


@pytest.mark.asyncio
async def test_gc_once_updates_last_result_with_ran_at(monkeypatch, mock_controller):
    """RGA-S5 precondition: gc_once 正常执行后 _last_gc_result 含 ran_at。"""
    pool = _FakePool([_row("REQ-1", "analyzing")])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()

    last = runner_gc.get_last_result()
    assert last is not None
    assert "ran_at" in last
    assert "cleaned_pods" in last
    assert "cleaned_pvcs" in last


@pytest.mark.asyncio
async def test_gc_once_skipped_also_updates_last_result():
    """no controller 时 skipped 结果也更新 _last_gc_result（含 ran_at）。"""
    k8s_runner.set_controller(None)

    await runner_gc.gc_once()

    last = runner_gc.get_last_result()
    assert last is not None
    assert "skipped" in last
    assert "ran_at" in last
