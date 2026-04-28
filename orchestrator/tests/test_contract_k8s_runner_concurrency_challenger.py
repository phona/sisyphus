"""Contract tests for k8s-runner-concurrency (REQ-k8s-concurrent-runner-race-1777339786).

Black-box challenger. Does NOT read k8s_runner.py implementation. Derived from:
  openspec/changes/REQ-k8s-concurrent-runner-race-1777339786/specs/k8s-runner-concurrency/spec.md

Scenarios:
  KRRACE-S1  two concurrent ensure_runner calls both succeed without ApiException(status=0)
  KRRACE-S2  _k8s_api_lock is present as an asyncio.Lock on every RunnerController instance

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import asyncio
import threading
import time

from kubernetes.client.exceptions import ApiException


def _make_ready_pod(pod_name: str):
    """Return a fake pod object whose status is Ready."""
    from kubernetes import client as k8s_client

    cond = k8s_client.V1PodCondition(type="Ready", status="True")
    status = k8s_client.V1PodStatus(phase="Running", conditions=[cond])
    meta = k8s_client.V1ObjectMeta(name=pod_name)
    return k8s_client.V1Pod(metadata=meta, status=status)


class _ThreadUnsafeCoreV1Api:
    """Raises ApiException(status=0) if any two methods execute concurrently in threads.

    This simulates the thread-unsafe behaviour of kubernetes-python's ApiClient
    when shared across concurrent asyncio.to_thread calls.
    """

    def __init__(self):
        self._active = 0
        self._counter_lock = threading.Lock()

    def _enter(self):
        with self._counter_lock:
            self._active += 1
            if self._active > 1:
                self._active -= 1
                raise ApiException(status=0, reason="concurrent thread access detected by fake")
        # Hold briefly so a concurrent call has a chance to observe the overlap.
        time.sleep(0.01)

    def _leave(self):
        with self._counter_lock:
            self._active -= 1

    def create_namespaced_persistent_volume_claim(self, namespace, body, **kwargs):
        self._enter()
        try:
            return object()
        finally:
            self._leave()

    def create_namespaced_pod(self, namespace, body, **kwargs):
        self._enter()
        try:
            return object()
        finally:
            self._leave()

    def read_namespaced_pod_status(self, pod_name, namespace, **kwargs):
        self._enter()
        try:
            return _make_ready_pod(pod_name)
        finally:
            self._leave()


def _make_controller(core_v1=None):
    """Construct a RunnerController with minimal valid params for testing.

    Always passes core_v1 to skip kubeconfig loading (which is unavailable in CI).
    """
    from unittest.mock import MagicMock

    from orchestrator.k8s_runner import RunnerController

    return RunnerController(
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner:main",
        runner_sa="sisyphus-runner",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="sisyphus-runner-secret",
        in_cluster=False,
        ready_timeout_sec=10,
        ready_attempts=1,
        core_v1=core_v1 if core_v1 is not None else MagicMock(),
    )


# ── KRRACE-S1 ──────────────────────────────────────────────────────────────


async def test_krrace_s1_concurrent_ensure_runner_no_apiexception_status0():
    """KRRACE-S1: asyncio.gather of two concurrent ensure_runner calls must both succeed.

    A thread-unsafe fake CoreV1Api raises ApiException(status=0) when any two of its
    methods execute in separate threads at the same time. With _k8s_api_lock serializing
    all core_v1 calls the fake never sees concurrent access, and both coroutines must
    return the correct pod names without raising any exception.
    """
    fragile = _ThreadUnsafeCoreV1Api()
    ctrl = _make_controller(core_v1=fragile)

    result_a, result_b = await asyncio.gather(
        ctrl.ensure_runner("REQ-A", wait_ready=True, timeout_sec=10, attempts=1),
        ctrl.ensure_runner("REQ-B", wait_ready=True, timeout_sec=10, attempts=1),
    )

    assert result_a == "runner-req-a"
    assert result_b == "runner-req-b"


# ── KRRACE-S2 ──────────────────────────────────────────────────────────────


def test_krrace_s2_k8s_api_lock_is_asyncio_lock():
    """KRRACE-S2: every RunnerController instance must expose _k8s_api_lock as asyncio.Lock."""
    ctrl = _make_controller()
    assert isinstance(ctrl._k8s_api_lock, asyncio.Lock), (
        f"expected asyncio.Lock, got {type(ctrl._k8s_api_lock)}"
    )
