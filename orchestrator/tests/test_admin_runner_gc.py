"""admin runner-gc endpoints 单测（REQ-430）。

覆盖场景：
  RGA-S1 POST /admin/runner-gc 正常，返回 split 结果 + ran_at
  RGA-S2 POST /admin/runner-gc 无 controller → {skipped, ran_at}
  RGA-S3 POST /admin/runner-gc 无 token → 401
  RGA-S4 GET /admin/runner-gc/status GC 前 → {last: null}
  RGA-S5 GET /admin/runner-gc/status GC 后 → {last: {..., ran_at}}
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from orchestrator import admin as admin_mod
from orchestrator import k8s_runner, runner_gc
from orchestrator.webhook import _verify_token as real_verify_token


@pytest.fixture(autouse=True)
def _reset_last_gc_result():
    """隔离 _last_gc_result 跨 test 污染。"""
    runner_gc._last_gc_result = None
    yield
    runner_gc._last_gc_result = None


@pytest.fixture
def mock_controller(monkeypatch):
    fake = MagicMock()
    fake.gc_orphan_pods = AsyncMock(return_value=[])
    fake.gc_orphan_pvcs = AsyncMock(return_value=[])
    fake.node_disk_usage_ratio = AsyncMock(return_value=0.3)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.fixture
def _skip_token(monkeypatch):
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args):
        return self._rows


# ── RGA-S1 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_runner_gc_returns_split_result_with_ran_at(
    monkeypatch, mock_controller, _skip_token
):
    """RGA-S1: POST /admin/runner-gc 返回 cleaned_pods + cleaned_pvcs + ran_at。"""
    pool = _FakePool([{"req_id": "REQ-1", "state": "analyzing",
                       "updated_at": None, "context": {}}])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    mock_controller.gc_orphan_pods = AsyncMock(return_value=["runner-req-x"])
    mock_controller.gc_orphan_pvcs = AsyncMock(return_value=[])

    result = await admin_mod.trigger_runner_gc(authorization="Bearer x")

    assert result["cleaned_pods"] == ["runner-req-x"]
    assert result["cleaned_pvcs"] == []
    assert "ran_at" in result
    assert result["ran_at"]  # non-empty


# ── RGA-S2 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_runner_gc_no_controller_returns_skipped(_skip_token):
    """RGA-S2: 无 K8s controller 时返 {skipped, ran_at}，不抛异常。"""
    k8s_runner.set_controller(None)

    result = await admin_mod.trigger_runner_gc(authorization="Bearer x")

    assert "skipped" in result
    assert "ran_at" in result


# ── RGA-S3 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_runner_gc_missing_token_raises_401(monkeypatch, mock_controller):
    """RGA-S3: Bearer token 缺失 → 401（_verify_token 抛 HTTPException）。"""
    monkeypatch.setattr(admin_mod, "_verify_token", real_verify_token)

    with pytest.raises(HTTPException) as ei:
        await admin_mod.trigger_runner_gc(authorization=None)
    assert ei.value.status_code == 401


# ── RGA-S4 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runner_gc_status_null_before_any_gc():
    """RGA-S4: 首次 GC 前 /admin/runner-gc/status 返 {last: null}。"""
    result = await admin_mod.runner_gc_status()
    assert result == {"last": None}


# ── RGA-S5 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runner_gc_status_after_gc_contains_ran_at(
    monkeypatch, mock_controller, _skip_token
):
    """RGA-S5: GC 触发后 /admin/runner-gc/status 返含 ran_at 的 last 字段。"""
    pool = _FakePool([{"req_id": "REQ-1", "state": "analyzing",
                       "updated_at": None, "context": {}}])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    # 先触发 GC
    await admin_mod.trigger_runner_gc(authorization="Bearer x")

    # 再查 status
    status = await admin_mod.runner_gc_status()
    assert status["last"] is not None
    assert "ran_at" in status["last"]
    assert "cleaned_pods" in status["last"]
