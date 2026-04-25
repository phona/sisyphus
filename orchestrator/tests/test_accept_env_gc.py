"""accept_env_gc.gc_once 단테스트：mock PG pool + k8s_runner controller。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes.client import ApiException

from orchestrator import accept_env_gc, k8s_runner


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args):
        return self._rows


def _row(req_id, state, updated_at=None):
    return {"req_id": req_id, "state": state, "updated_at": updated_at}


def _ns(name):
    ns = MagicMock()
    ns.metadata.name = name
    return ns


@pytest.fixture(autouse=True)
def _reset_rbac_flag():
    """각 테스트 전후 _NS_RBAC_DISABLED 리셋。"""
    accept_env_gc._NS_RBAC_DISABLED = False
    yield
    accept_env_gc._NS_RBAC_DISABLED = False


@pytest.fixture
def mock_controller(monkeypatch):
    fake = MagicMock()
    fake.core_v1.list_namespace = MagicMock(return_value=MagicMock(items=[]))
    fake.core_v1.delete_namespace = MagicMock()
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.mark.asyncio
async def test_skips_when_no_controller():
    k8s_runner.set_controller(None)
    result = await accept_env_gc.gc_once()
    assert "skipped" in result


@pytest.mark.asyncio
async def test_skips_when_rbac_disabled(mock_controller):
    accept_env_gc._NS_RBAC_DISABLED = True
    result = await accept_env_gc.gc_once()
    assert result.get("skipped") == "namespace rbac disabled"
    mock_controller.core_v1.list_namespace.assert_not_called()


@pytest.mark.asyncio
async def test_list_403_disables_gc(monkeypatch, mock_controller):
    pool = _FakePool([_row("REQ-1", "done")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.core_v1.list_namespace.side_effect = ApiException(status=403, reason="Forbidden")

    result = await accept_env_gc.gc_once()
    assert "skipped" in result
    assert accept_env_gc._NS_RBAC_DISABLED is True


@pytest.mark.asyncio
async def test_done_req_ns_deleted(monkeypatch, mock_controller):
    """done 상태 REQ의 accept namespace는 즉시 삭제。"""
    pool = _FakePool([_row("REQ-foo-123", "done")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.core_v1.list_namespace.return_value = MagicMock(
        items=[_ns("accept-req-foo-123"), _ns("other-ns"), _ns("kube-system")]
    )

    result = await accept_env_gc.gc_once()
    assert "accept-req-foo-123" in result["cleaned"]
    mock_controller.core_v1.delete_namespace.assert_called_once_with("accept-req-foo-123")


@pytest.mark.asyncio
async def test_inflight_ns_not_deleted(monkeypatch, mock_controller):
    """진행 중인 REQ의 namespace는 삭제하지 않음。"""
    pool = _FakePool([_row("REQ-foo-123", "accept-running")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.core_v1.list_namespace.return_value = MagicMock(
        items=[_ns("accept-req-foo-123")]
    )

    result = await accept_env_gc.gc_once()
    assert result["cleaned"] == []
    mock_controller.core_v1.delete_namespace.assert_not_called()


@pytest.mark.asyncio
async def test_escalated_within_retention_not_deleted(monkeypatch, mock_controller):
    """escalated이지만 retention 기간 내 → 삭제하지 않음。"""
    recent = datetime.now(UTC) - timedelta(hours=2)
    pool = _FakePool([_row("REQ-foo-123", "escalated", updated_at=recent)])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.core_v1.list_namespace.return_value = MagicMock(
        items=[_ns("accept-req-foo-123")]
    )

    result = await accept_env_gc.gc_once()
    assert result["cleaned"] == []
    mock_controller.core_v1.delete_namespace.assert_not_called()


@pytest.mark.asyncio
async def test_escalated_past_retention_deleted(monkeypatch, mock_controller):
    """escalated이고 retention 초과 → namespace 삭제。"""
    old = datetime.now(UTC) - timedelta(days=30)
    pool = _FakePool([_row("REQ-foo-123", "escalated", updated_at=old)])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.core_v1.list_namespace.return_value = MagicMock(
        items=[_ns("accept-req-foo-123")]
    )

    result = await accept_env_gc.gc_once()
    assert "accept-req-foo-123" in result["cleaned"]


@pytest.mark.asyncio
async def test_non_accept_ns_ignored(monkeypatch, mock_controller):
    """accept-req-* 패턴이 아닌 namespace는 무시。"""
    pool = _FakePool([_row("REQ-foo-123", "done")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.core_v1.list_namespace.return_value = MagicMock(
        items=[_ns("kube-system"), _ns("sisyphus-runners"), _ns("default")]
    )

    result = await accept_env_gc.gc_once()
    assert result["cleaned"] == []
    mock_controller.core_v1.delete_namespace.assert_not_called()


@pytest.mark.asyncio
async def test_delete_404_treated_as_idempotent(monkeypatch, mock_controller):
    """삭제 시 404 → 이미 삭제된 것, 정상 처리。"""
    pool = _FakePool([_row("REQ-foo-123", "done")])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)
    mock_controller.core_v1.list_namespace.return_value = MagicMock(
        items=[_ns("accept-req-foo-123")]
    )
    mock_controller.core_v1.delete_namespace.side_effect = ApiException(status=404, reason="NotFound")

    result = await accept_env_gc.gc_once()
    # 404는 오류로 취급하지 않음 (이미 삭제된 상태)
    assert result.get("error") is None


def test_req_lower_from_ns():
    """네임스페이스 이름에서 req_lower 추출 로직 검증。"""
    assert accept_env_gc._req_lower_from_ns("accept-req-foo-123") == "req-foo-123"
    assert accept_env_gc._req_lower_from_ns("accept-req-accept-env-gc-minimal-1777138424") == "req-accept-env-gc-minimal-1777138424"
    assert accept_env_gc._req_lower_from_ns("kube-system") is None
    assert accept_env_gc._req_lower_from_ns("accept-something-else") is None  # not "accept-req-"
    assert accept_env_gc._req_lower_from_ns("default") is None
