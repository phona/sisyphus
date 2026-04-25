"""Challenger contract tests for REQ-accept-env-gc-minimal-1777138424.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-accept-env-gc-minimal-1777138424/specs/accept-env-gc/spec.md
  openspec/changes/REQ-accept-env-gc-minimal-1777138424/specs/accept-env-gc/contract.spec.yaml

Scenarios covered:
  AEGC-S1  done REQ → delete_namespace called; ns in cleaned list
  AEGC-S2  in-flight REQ → delete_namespace NOT called; cleaned is empty
  AEGC-S3  escalated + within retention window → NOT deleted
  AEGC-S4  escalated + past retention window → deleted
  AEGC-S5  no runner controller → {"skipped": "no runner controller"}
  AEGC-S6  list_namespace raises ApiException(403) → _NS_RBAC_DISABLED=True, INFO log
  AEGC-S7  _NS_RBAC_DISABLED=True → list_namespace skipped; {"skipped": "namespace rbac disabled"}
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from kubernetes.client import ApiException

from orchestrator import accept_env_gc, k8s_runner


# ─── helpers ───────────────────────────────────────────────────────────


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args):
        return self._rows


def _row(req_id: str, state: str, updated_at: datetime | None = None) -> dict:
    return {
        "req_id": req_id,
        "state": state,
        "updated_at": updated_at or datetime.now(UTC),
        "context": {},
    }


def _ns_item(name: str) -> MagicMock:
    item = MagicMock()
    item.metadata.name = name
    return item


def _ns_list(*names: str) -> MagicMock:
    obj = MagicMock()
    obj.items = [_ns_item(n) for n in names]
    return obj


# ─── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def mock_core_v1():
    """Return a fake CoreV1Api instance with list_namespace / delete_namespace."""
    fake = MagicMock()
    fake.list_namespace = MagicMock(return_value=_ns_list())
    fake.delete_namespace = MagicMock(return_value=MagicMock())
    return fake


@pytest.fixture()
def mock_controller(mock_core_v1):
    """Inject a fake k8s_runner controller whose .core_v1 is our fake CoreV1Api."""
    fake_ctrl = MagicMock()
    fake_ctrl.core_v1 = mock_core_v1
    k8s_runner.set_controller(fake_ctrl)
    yield fake_ctrl
    k8s_runner.set_controller(None)


@pytest.fixture(autouse=True)
def _reset_rbac_flag():
    """Reset _NS_RBAC_DISABLED between tests."""
    accept_env_gc._NS_RBAC_DISABLED = False
    yield
    accept_env_gc._NS_RBAC_DISABLED = False


# ─── AEGC-S1: done REQ → namespace deleted, in cleaned list ────────────


@pytest.mark.asyncio
async def test_aegc_s1_done_req_namespace_deleted(monkeypatch, mock_controller, mock_core_v1):
    """AEGC-S1: done REQ → delete_namespace("accept-req-foo-123") called."""
    pool = _FakePool([_row("REQ-foo-123", "done")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_core_v1.list_namespace.return_value = _ns_list("accept-req-foo-123")

    result = await accept_env_gc.gc_once()

    mock_core_v1.delete_namespace.assert_called_once_with("accept-req-foo-123")
    assert "accept-req-foo-123" in result.get("cleaned", [])


# ─── AEGC-S2: in-flight REQ → no deletion ─────────────────────────────


@pytest.mark.asyncio
async def test_aegc_s2_inflight_req_not_deleted(monkeypatch, mock_controller, mock_core_v1):
    """AEGC-S2: in-flight (accept-running) REQ → delete_namespace NOT called."""
    pool = _FakePool([_row("REQ-foo-123", "accept-running")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_core_v1.list_namespace.return_value = _ns_list("accept-req-foo-123")

    result = await accept_env_gc.gc_once()

    mock_core_v1.delete_namespace.assert_not_called()
    assert result.get("cleaned", []) == []


# ─── AEGC-S3: escalated within retention → no deletion ────────────────


@pytest.mark.asyncio
async def test_aegc_s3_escalated_within_retention_not_deleted(
    monkeypatch, mock_controller, mock_core_v1
):
    """AEGC-S3: escalated + 2h ago + default 1d retention → NOT deleted."""
    recent = datetime.now(UTC) - timedelta(hours=2)
    pool = _FakePool([_row("REQ-foo-123", "escalated", updated_at=recent)])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_core_v1.list_namespace.return_value = _ns_list("accept-req-foo-123")

    result = await accept_env_gc.gc_once()

    mock_core_v1.delete_namespace.assert_not_called()
    assert "accept-req-foo-123" not in result.get("cleaned", [])


# ─── AEGC-S4: escalated past retention → deleted ──────────────────────


@pytest.mark.asyncio
async def test_aegc_s4_escalated_past_retention_deleted(
    monkeypatch, mock_controller, mock_core_v1
):
    """AEGC-S4: escalated + 30d ago → delete_namespace called."""
    old = datetime.now(UTC) - timedelta(days=30)
    pool = _FakePool([_row("REQ-foo-123", "escalated", updated_at=old)])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_core_v1.list_namespace.return_value = _ns_list("accept-req-foo-123")

    result = await accept_env_gc.gc_once()

    mock_core_v1.delete_namespace.assert_called_once_with("accept-req-foo-123")
    assert "accept-req-foo-123" in result.get("cleaned", [])


# ─── AEGC-S5: no runner controller → skipped ──────────────────────────


@pytest.mark.asyncio
async def test_aegc_s5_no_controller_skipped():
    """AEGC-S5: no runner controller → {"skipped": "no runner controller"}, no K8s calls."""
    k8s_runner.set_controller(None)
    result = await accept_env_gc.gc_once()

    assert "skipped" in result
    assert "no runner controller" in result["skipped"]


# ─── AEGC-S6: list_namespace 403 → disable flag + INFO log ────────────


@pytest.mark.asyncio
async def test_aegc_s6_list_namespace_403_disables_flag(
    monkeypatch, mock_controller, mock_core_v1, capsys
):
    """AEGC-S6: list_namespace ApiException(403) → _NS_RBAC_DISABLED=True, one INFO log."""
    mock_core_v1.list_namespace.side_effect = ApiException(status=403, reason="Forbidden")
    pool = _FakePool([])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    assert accept_env_gc._NS_RBAC_DISABLED is False
    result = await accept_env_gc.gc_once()

    assert accept_env_gc._NS_RBAC_DISABLED is True, "_NS_RBAC_DISABLED must be set True on 403"
    assert "skipped" in result, "result must contain 'skipped' key after 403"

    out = capsys.readouterr().out
    assert "accept_env_gc.rbac_denied" in out, (
        "INFO log 'accept_env_gc.rbac_denied' must be emitted once on first 403"
    )


# ─── AEGC-S7: _NS_RBAC_DISABLED=True → list_namespace not called ──────


@pytest.mark.asyncio
async def test_aegc_s7_rbac_disabled_skips_list_namespace(
    monkeypatch, mock_controller, mock_core_v1
):
    """AEGC-S7: _NS_RBAC_DISABLED=True → list_namespace never called."""
    accept_env_gc._NS_RBAC_DISABLED = True
    pool = _FakePool([])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    result = await accept_env_gc.gc_once()

    mock_core_v1.list_namespace.assert_not_called()
    assert "skipped" in result
    assert "namespace rbac disabled" in result["skipped"]
