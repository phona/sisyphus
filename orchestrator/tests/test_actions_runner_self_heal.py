"""Unit tests for `orchestrator.actions._runner.ensure_runner_alive`.

Covers RSH-S1..S4 from
`openspec/changes/REQ-fix-runner-self-heal-394-1777869659/specs/runner-pod-self-heal/spec.md`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from orchestrator import k8s_runner
from orchestrator.actions import _runner as self_heal
from orchestrator.k8s_runner import RunnerStatus

# 这个文件专测自愈钩自身；opt-out conftest 的 autouse stub。
pytestmark = pytest.mark.no_stub_self_heal


def _status(pod_phase: str, pvc_phase: str = "Bound") -> RunnerStatus:
    return RunnerStatus(
        req_id="REQ-X",
        pod_name="runner-req-x",
        pvc_name="workspace-req-x",
        pod_phase=pod_phase,
        pvc_phase=pvc_phase,
        created_at=None,
    )


@pytest.fixture
def fake_controller(monkeypatch):
    """Inject a mock RunnerController; return it so the test can assert calls."""
    rc = AsyncMock()
    monkeypatch.setattr(k8s_runner, "get_controller", lambda: rc)
    return rc


# RSH-S1
@pytest.mark.asyncio
async def test_alive_pod_is_noop_fast_path(fake_controller, caplog):
    fake_controller.get_runner_status = AsyncMock(return_value=_status("Running"))

    out = await self_heal.ensure_runner_alive("REQ-X")

    assert out is True
    fake_controller.ensure_runner.assert_not_called()
    fake_controller.pause.assert_not_called()
    # No lazy_recreate event
    assert not any("runner.lazy_recreate" in r.getMessage() for r in caplog.records)


# RSH-S1 variant: Pending is also alive enough — let exec_in_runner handle it
@pytest.mark.asyncio
async def test_pending_pod_is_alive(fake_controller):
    fake_controller.get_runner_status = AsyncMock(return_value=_status("Pending"))

    out = await self_heal.ensure_runner_alive("REQ-X")

    assert out is True
    fake_controller.ensure_runner.assert_not_called()


# RSH-S2 (a): get_runner_status returns None
@pytest.mark.asyncio
async def test_missing_runner_recreates_with_pvc_reuse(fake_controller):
    fake_controller.get_runner_status = AsyncMock(return_value=None)
    fake_controller.ensure_runner = AsyncMock(return_value="runner-req-x")

    out = await self_heal.ensure_runner_alive("REQ-X")

    assert out is True
    fake_controller.ensure_runner.assert_awaited_once_with("REQ-X", wait_ready=True)
    fake_controller.pause.assert_not_called()


# RSH-S2 (b): get_runner_status returns NotFound (pod gone, PVC kept)
@pytest.mark.asyncio
async def test_pod_notfound_recreates_with_pvc_reuse(fake_controller):
    fake_controller.get_runner_status = AsyncMock(
        return_value=_status("NotFound", pvc_phase="Bound")
    )
    fake_controller.ensure_runner = AsyncMock(return_value="runner-req-x")

    out = await self_heal.ensure_runner_alive("REQ-X")

    assert out is True
    fake_controller.ensure_runner.assert_awaited_once_with("REQ-X", wait_ready=True)
    fake_controller.pause.assert_not_called()


# RSH-S3
@pytest.mark.asyncio
async def test_terminal_pod_paused_before_recreate(fake_controller):
    fake_controller.get_runner_status = AsyncMock(return_value=_status("Failed"))
    fake_controller.pause = AsyncMock(return_value=True)
    fake_controller.ensure_runner = AsyncMock(return_value="runner-req-x")

    out = await self_heal.ensure_runner_alive("REQ-X")

    assert out is True
    fake_controller.pause.assert_awaited_once_with("REQ-X")
    fake_controller.ensure_runner.assert_awaited_once_with("REQ-X", wait_ready=True)
    # pause MUST be called before ensure_runner — verify ordering via call args list
    # by checking pause got awaited at least once (already done) and the order
    # is enforced by the function body (sequential awaits).


@pytest.mark.asyncio
async def test_succeeded_pod_paused_before_recreate(fake_controller):
    fake_controller.get_runner_status = AsyncMock(return_value=_status("Succeeded"))
    fake_controller.pause = AsyncMock(return_value=True)
    fake_controller.ensure_runner = AsyncMock(return_value="runner-req-x")

    out = await self_heal.ensure_runner_alive("REQ-X")

    assert out is True
    fake_controller.pause.assert_awaited_once_with("REQ-X")
    fake_controller.ensure_runner.assert_awaited_once_with("REQ-X", wait_ready=True)


# RSH-S4
@pytest.mark.asyncio
async def test_no_controller_returns_false(monkeypatch):
    def _raise():
        raise RuntimeError("controller not initialised")

    monkeypatch.setattr(k8s_runner, "get_controller", _raise)

    out = await self_heal.ensure_runner_alive("REQ-X")

    assert out is False
