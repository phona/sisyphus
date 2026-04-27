"""Contract tests for runner-gc-pod-pvc-split (REQ-runner-gc-pod-pvc-split-1777283946).

Black-box challenger. Does NOT read implementation code (runner_gc.py / k8s_runner.py).
Derived from:
  openspec/changes/REQ-runner-gc-pod-pvc-split-1777283946/specs/runner-gc-pod-pvc-split/spec.md

Scenarios:
  RGS-S1  escalated REQ within retention → in PVC keep set but NOT in Pod keep set
  RGS-S2  disk pressure forces escalated PVC out of keep set; Pod keep set still excludes terminal
  RGS-S3  in-flight REQs in both keep sets; done REQs in neither
  RGS-S4  gc_orphan_pods deletes Pods not in keep set, leaves PVCs alone
  RGS-S5  gc_orphan_pvcs deletes PVCs not in keep set, leaves Pods alone
  RGS-S6  gc_once with disk-check 403 sets flag and both sweeps still run

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes.client import ApiException

from orchestrator import k8s_runner, runner_gc
from orchestrator.k8s_runner import RunnerController

# ─── Shared helpers ───────────────────────────────────────────────────────────


def _row(req_id: str, state: str, updated_at=None) -> dict:
    return {"req_id": req_id, "state": state, "updated_at": updated_at, "context": {}}


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args):
        return self._rows


def _make_pod_item(req_id: str) -> MagicMock:
    item = MagicMock()
    item.metadata.name = f"runner-{req_id.lower()}"
    item.metadata.labels = {"sisyphus/req-id": req_id.lower(), "sisyphus/role": "runner"}
    return item


def _make_pvc_item(req_id: str) -> MagicMock:
    item = MagicMock()
    item.metadata.name = f"workspace-{req_id.lower()}"
    item.metadata.labels = {"sisyphus/req-id": req_id.lower(), "sisyphus/role": "workspace"}
    return item


def _make_controller(core_v1: MagicMock | None = None) -> RunnerController:
    return RunnerController(
        namespace="sisyphus-runners",
        runner_image="ghcr.io/test/runner:latest",
        runner_sa="sisyphus-runner-sa",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="sisyphus-runner-secrets",
        image_pull_secrets=[],
        ready_timeout_sec=5,
        core_v1=core_v1 or MagicMock(),
    )


@pytest.fixture
def mock_controller(monkeypatch):
    fake = MagicMock()
    fake.gc_orphan_pods = AsyncMock(return_value=[])
    fake.gc_orphan_pvcs = AsyncMock(return_value=[])
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


@pytest.fixture(autouse=True)
def _reset_disk_flag():
    runner_gc._DISK_CHECK_DISABLED = False
    yield
    runner_gc._DISK_CHECK_DISABLED = False


# ─── RGS-S1 ──────────────────────────────────────────────────────────────────


async def test_rgs_s1_escalated_within_retention_in_pvc_keep_not_in_pod_keep(
    monkeypatch, mock_controller
):
    """RGS-S1: escalated REQ within retention window → excluded from Pod keep set,
    included in PVC keep set, and result dict has both cleaned_pods/cleaned_pvcs keys.
    """
    recent = datetime.now(UTC) - timedelta(hours=2)  # well within default 1-day window
    pool = _FakePool([_row("REQ-GC-1", "escalated", updated_at=recent)])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    result = await runner_gc.gc_once()

    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]

    assert "REQ-GC-1" not in pod_keep, (
        "escalated REQ within retention MUST NOT be in Pod keep set — "
        "Pod holds 512Mi memory and must be reclaimed immediately to free scheduling capacity"
    )
    assert "REQ-GC-1" in pvc_keep, (
        "escalated REQ within retention MUST be in PVC keep set — "
        "PVC is needed for human-debug workflow during retention window"
    )
    assert "cleaned_pods" in result, "result dict MUST contain 'cleaned_pods' key"
    assert "cleaned_pvcs" in result, "result dict MUST contain 'cleaned_pvcs' key"


# ─── RGS-S2 ──────────────────────────────────────────────────────────────────


async def test_rgs_s2_disk_pressure_evicts_escalated_pvc_pod_keep_still_empty(
    monkeypatch, mock_controller
):
    """RGS-S2: disk pressure (ratio=0.9) → escalated-within-retention PVC also evicted;
    Pod keep set is empty regardless (terminal states never kept); disk_pressure=True.
    """
    recent = datetime.now(UTC) - timedelta(hours=2)
    pool = _FakePool([_row("REQ-GC-1", "escalated", updated_at=recent)])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    mock_controller.node_disk_usage_ratio = AsyncMock(return_value=0.9)

    result = await runner_gc.gc_once()

    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]

    assert pod_keep == set(), (
        "Pod keep set MUST be empty — terminal states (escalated) are NEVER kept "
        "in Pod keep set, regardless of disk pressure"
    )
    assert pvc_keep == set(), (
        "disk pressure MUST evict escalated PVC retention — emergency evacuation "
        "waives the human-debug retention window"
    )
    assert result.get("disk_pressure") is True, (
        "result MUST contain disk_pressure=True when disk ratio exceeds threshold"
    )


# ─── RGS-S3 ──────────────────────────────────────────────────────────────────


async def test_rgs_s3_inflight_in_both_keep_sets_done_in_neither(
    monkeypatch, mock_controller
):
    """RGS-S3: in-flight REQs → both keep sets; done REQ → neither keep set."""
    pool = _FakePool([
        _row("REQ-A", "analyzing"),
        _row("REQ-B", "staging-test-running"),
        _row("REQ-C", "done", updated_at=datetime.now(UTC)),
    ])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)

    await runner_gc.gc_once()

    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]

    assert pod_keep == {"REQ-A", "REQ-B"}, (
        f"Pod keep set MUST be exactly {{REQ-A, REQ-B}}, got {pod_keep!r}"
    )
    assert pvc_keep == {"REQ-A", "REQ-B"}, (
        f"PVC keep set MUST be exactly {{REQ-A, REQ-B}}, got {pvc_keep!r}"
    )
    assert "REQ-C" not in pod_keep, "done REQ MUST NOT appear in Pod keep set"
    assert "REQ-C" not in pvc_keep, "done REQ MUST NOT appear in PVC keep set"


# ─── RGS-S4 ──────────────────────────────────────────────────────────────────


async def test_rgs_s4_gc_orphan_pods_deletes_non_keep_leaves_pvcs_untouched():
    """RGS-S4: gc_orphan_pods(keep={REQ-1}) with 3 Pods → deletes REQ-2 and REQ-3 Pods,
    MUST NOT call delete_namespaced_persistent_volume_claim.
    """
    core_v1 = MagicMock()

    pod_list = MagicMock()
    pod_list.items = [
        _make_pod_item("req-1"),
        _make_pod_item("req-2"),
        _make_pod_item("req-3"),
    ]
    core_v1.list_namespaced_pod.return_value = pod_list
    core_v1.delete_namespaced_pod.return_value = MagicMock()

    rc = _make_controller(core_v1)
    deleted = await rc.gc_orphan_pods({"REQ-1"})

    # MUST list Pods by sisyphus/role=runner label selector
    core_v1.list_namespaced_pod.assert_called_once()
    list_call = str(core_v1.list_namespaced_pod.call_args)
    assert "sisyphus/role=runner" in list_call, (
        f"gc_orphan_pods MUST list Pods with label_selector='sisyphus/role=runner'; "
        f"got call_args={list_call}"
    )

    # MUST delete exactly 2 Pods (req-2 and req-3)
    assert core_v1.delete_namespaced_pod.call_count == 2, (
        f"delete_namespaced_pod MUST be called exactly 2 times; "
        f"got {core_v1.delete_namespaced_pod.call_count}"
    )

    # MUST NOT touch PVCs
    core_v1.delete_namespaced_persistent_volume_claim.assert_not_called()

    # returned list MUST contain the deleted REQ ids
    deleted_lower = {r.lower() for r in deleted}
    assert deleted_lower == {"req-2", "req-3"}, (
        f"returned list MUST contain exactly req-2 and req-3; got {deleted!r}"
    )


# ─── RGS-S5 ──────────────────────────────────────────────────────────────────


async def test_rgs_s5_gc_orphan_pvcs_deletes_non_keep_leaves_pods_untouched():
    """RGS-S5: gc_orphan_pvcs(keep={REQ-1}) with 3 PVCs → deletes REQ-2 and REQ-3 PVCs,
    MUST NOT call delete_namespaced_pod.
    """
    core_v1 = MagicMock()

    pvc_list = MagicMock()
    pvc_list.items = [
        _make_pvc_item("req-1"),
        _make_pvc_item("req-2"),
        _make_pvc_item("req-3"),
    ]
    core_v1.list_namespaced_persistent_volume_claim.return_value = pvc_list
    core_v1.delete_namespaced_persistent_volume_claim.return_value = MagicMock()

    rc = _make_controller(core_v1)
    deleted = await rc.gc_orphan_pvcs({"REQ-1"})

    # MUST list PVCs by sisyphus/role=workspace label selector
    core_v1.list_namespaced_persistent_volume_claim.assert_called_once()
    list_call = str(core_v1.list_namespaced_persistent_volume_claim.call_args)
    assert "sisyphus/role=workspace" in list_call, (
        f"gc_orphan_pvcs MUST list PVCs with label_selector='sisyphus/role=workspace'; "
        f"got call_args={list_call}"
    )

    # MUST delete exactly 2 PVCs (req-2 and req-3)
    assert core_v1.delete_namespaced_persistent_volume_claim.call_count == 2, (
        f"delete_namespaced_persistent_volume_claim MUST be called exactly 2 times; "
        f"got {core_v1.delete_namespaced_persistent_volume_claim.call_count}"
    )

    # MUST NOT touch Pods
    core_v1.delete_namespaced_pod.assert_not_called()

    # returned list MUST contain the deleted REQ ids
    deleted_lower = {r.lower() for r in deleted}
    assert deleted_lower == {"req-2", "req-3"}, (
        f"returned list MUST contain exactly req-2 and req-3; got {deleted!r}"
    )


# ─── RGS-S6 ──────────────────────────────────────────────────────────────────


async def test_rgs_s6_disk_check_403_sets_flag_emits_info_log_both_sweeps_run(
    monkeypatch, mock_controller, capsys
):
    """RGS-S6: ApiException(status=403) from node_disk_usage_ratio → _DISK_CHECK_DISABLED
    set to True, exactly one INFO log 'runner_gc.disk_check_rbac_denied' emitted,
    disk_pressure=False, and both gc_orphan_pods/gc_orphan_pvcs still invoked with
    the analyzing REQ in their keep sets.
    """
    pool = _FakePool([_row("REQ-Z", "analyzing")])
    monkeypatch.setattr("orchestrator.runner_gc.db.get_pool", lambda: pool)
    mock_controller.node_disk_usage_ratio = AsyncMock(
        side_effect=ApiException(status=403, reason="Forbidden")
    )

    assert runner_gc._DISK_CHECK_DISABLED is False, "precondition: flag must start False"

    result = await runner_gc.gc_once()

    assert result.get("disk_pressure") is False, (
        "403 RBAC denial MUST result in disk_pressure=False — it is NOT treated as disk pressure"
    )
    assert runner_gc._DISK_CHECK_DISABLED is True, (
        "first ApiException(status=403) MUST set process-level _DISK_CHECK_DISABLED=True "
        "so subsequent ticks short-circuit the disk probe"
    )

    out = capsys.readouterr().out
    assert "disk_check_rbac_denied" in out, (
        "MUST emit exactly one INFO log with event 'runner_gc.disk_check_rbac_denied'; "
        f"stdout was: {out!r}"
    )

    # Both sweeps MUST still run after disk-check 403
    mock_controller.gc_orphan_pods.assert_awaited_once()
    mock_controller.gc_orphan_pvcs.assert_awaited_once()

    pod_keep = mock_controller.gc_orphan_pods.await_args.args[0]
    pvc_keep = mock_controller.gc_orphan_pvcs.await_args.args[0]

    assert "REQ-Z" in pod_keep, (
        "analyzing REQ MUST be in Pod keep set even after disk-check 403"
    )
    assert "REQ-Z" in pvc_keep, (
        "analyzing REQ MUST be in PVC keep set even after disk-check 403"
    )
