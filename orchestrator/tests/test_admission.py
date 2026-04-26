"""admission.check_admission 单测：mock pool + k8s_runner controller。

覆盖 6 个 scenario（ORCH-RATE-S1..S6）+ fail-open 路径。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes.client import ApiException

from orchestrator import admission, k8s_runner, runner_gc


class _FakePool:
    def __init__(self, count: int = 0, raise_exc: Exception | None = None):
        self._count = count
        self._raise = raise_exc
        self.last_args: tuple | None = None

    async def fetchrow(self, sql, *args):
        self.last_args = args
        if self._raise is not None:
            raise self._raise
        return {"n": self._count}


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """每 case 还原 _DISK_CHECK_DISABLED + 卸掉 controller。"""
    runner_gc._DISK_CHECK_DISABLED = False
    k8s_runner.set_controller(None)
    yield
    runner_gc._DISK_CHECK_DISABLED = False
    k8s_runner.set_controller(None)


def _install_controller(*, ratio: float | None = None,
                        raise_exc: Exception | None = None) -> MagicMock:
    fake = MagicMock()
    if raise_exc is not None:
        fake.node_disk_usage_ratio = AsyncMock(side_effect=raise_exc)
    else:
        fake.node_disk_usage_ratio = AsyncMock(return_value=ratio if ratio is not None else 0.0)
    k8s_runner.set_controller(fake)
    return fake


# ─── Scenario S1: cap=0 disables in-flight gate ───────────────────────────


@pytest.mark.asyncio
async def test_inflight_cap_disabled_admits_any_count(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 0)
    pool = _FakePool(count=999)
    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is True
    assert decision.reason is None


# ─── Scenario S2: count under cap admits ──────────────────────────────────


@pytest.mark.asyncio
async def test_inflight_count_under_cap_admits(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    monkeypatch.setattr(admission.settings, "admission_disk_pressure_threshold", 0.75)
    _install_controller(ratio=0.10)
    pool = _FakePool(count=9)

    decision = await admission.check_admission(pool, req_id="REQ-new")

    assert decision.admit is True
    assert decision.reason is None
    # Verify SQL params: state list + req_id excluded
    assert pool.last_args is not None
    state_list, excluded_req_id = pool.last_args
    assert excluded_req_id == "REQ-new"
    assert "init" in state_list
    assert "done" in state_list
    assert "escalated" in state_list
    assert "gh-incident-open" in state_list


# ─── Scenario S3: count at cap rejects ────────────────────────────────────


@pytest.mark.asyncio
async def test_inflight_count_at_cap_rejects(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    pool = _FakePool(count=10)

    decision = await admission.check_admission(pool, req_id="REQ-new")

    assert decision.admit is False
    assert decision.reason is not None
    assert "inflight-cap-exceeded" in decision.reason
    assert "10/10" in decision.reason


@pytest.mark.asyncio
async def test_inflight_count_above_cap_rejects(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    pool = _FakePool(count=15)

    decision = await admission.check_admission(pool, req_id="REQ-new")

    assert decision.admit is False
    assert "inflight-cap-exceeded" in decision.reason
    assert "15/10" in decision.reason


# ─── Scenario S4: disk under threshold admits ─────────────────────────────


@pytest.mark.asyncio
async def test_disk_under_threshold_admits(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    monkeypatch.setattr(admission.settings, "admission_disk_pressure_threshold", 0.75)
    _install_controller(ratio=0.50)
    pool = _FakePool(count=0)

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is True


# ─── Scenario S5: disk above threshold rejects ────────────────────────────


@pytest.mark.asyncio
async def test_disk_above_threshold_rejects(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    monkeypatch.setattr(admission.settings, "admission_disk_pressure_threshold", 0.75)
    _install_controller(ratio=0.80)
    pool = _FakePool(count=0)

    decision = await admission.check_admission(pool, req_id="REQ-new")

    assert decision.admit is False
    assert "disk-pressure" in decision.reason
    assert "0.80" in decision.reason
    assert "0.75" in decision.reason


@pytest.mark.asyncio
async def test_disk_at_threshold_rejects(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    monkeypatch.setattr(admission.settings, "admission_disk_pressure_threshold", 0.75)
    _install_controller(ratio=0.75)
    pool = _FakePool(count=0)

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is False
    assert "disk-pressure" in decision.reason


# ─── Scenario S6: missing controller fails open ───────────────────────────


@pytest.mark.asyncio
async def test_no_controller_fails_open(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    # do NOT install controller; get_controller raises RuntimeError
    pool = _FakePool(count=0)

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is True


# ─── Fail-open: DB error on cap query ─────────────────────────────────────


@pytest.mark.asyncio
async def test_db_error_fails_open(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    _install_controller(ratio=0.10)
    pool = _FakePool(count=0, raise_exc=RuntimeError("pg down"))

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is True


# ─── Fail-open: disk probe RBAC 403 disables flag + admits ───────────────


@pytest.mark.asyncio
async def test_disk_rbac_denied_admits_and_disables_flag(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    _install_controller(raise_exc=ApiException(status=403, reason="Forbidden"))
    pool = _FakePool(count=0)

    assert runner_gc._DISK_CHECK_DISABLED is False
    decision = await admission.check_admission(pool, req_id="REQ-new")

    assert decision.admit is True
    assert runner_gc._DISK_CHECK_DISABLED is True


# ─── Fail-open: disk probe 500 admits but keeps flag alive ───────────────


@pytest.mark.asyncio
async def test_disk_probe_5xx_admits(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    _install_controller(raise_exc=ApiException(status=500, reason="Internal"))
    pool = _FakePool(count=0)

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is True
    assert runner_gc._DISK_CHECK_DISABLED is False


@pytest.mark.asyncio
async def test_disk_probe_other_exception_admits(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    _install_controller(raise_exc=RuntimeError("no node info"))
    pool = _FakePool(count=0)

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is True


# ─── Disk check short-circuits when GC flag already set ─────────────────


@pytest.mark.asyncio
async def test_disk_check_skipped_when_gc_flag_disabled(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    fake = _install_controller(ratio=0.99)
    runner_gc._DISK_CHECK_DISABLED = True
    pool = _FakePool(count=0)

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is True
    fake.node_disk_usage_ratio.assert_not_awaited()


# ─── Cap rejection short-circuits disk probe ─────────────────────────────


@pytest.mark.asyncio
async def test_cap_rejection_short_circuits_disk_probe(monkeypatch):
    monkeypatch.setattr(admission.settings, "inflight_req_cap", 10)
    fake = _install_controller(ratio=0.10)
    pool = _FakePool(count=10)

    decision = await admission.check_admission(pool, req_id="REQ-new")
    assert decision.admit is False
    assert "inflight-cap-exceeded" in decision.reason
    fake.node_disk_usage_ratio.assert_not_awaited()
