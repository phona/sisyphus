"""Challenger contract tests for REQ-fix-readyz-namespaced-1777808455.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-readyz-namespaced-1777808455/specs/readyz-namespaced/spec.md

Scenarios covered:
  RZN-S1  GET /readyz happy path → 200 + calls list_namespaced_pod once
          with namespace="sisyphus-runners", limit=1, _request_timeout=2;
          MUST NOT call list_namespace.
  RZN-S2  K8s API raises non-RuntimeError (e.g. ApiException) → 503,
          body.failed contains "k8s" but NOT "db" or "bkd".
  RZN-S3  k8s_runner.get_controller raises RuntimeError → 200,
          body.failed must NOT contain "k8s" (treat as skip).

Dev MUST NOT modify these tests to make them pass — fix the implementation
instead. If a test is wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ─────────────────────────────────────────────────────────────────────────────
# Black-box harness: patch the three probe surfaces that /readyz consults.
#
# The spec says /readyz probes K8s (target of this REQ), plus DB and BKD.
# All three must be made to succeed (or fail in controlled ways) without
# touching real infra. We patch at the module-import boundary of
# `orchestrator.main` so every probe call is intercepted regardless of
# internal helper layout.
# ─────────────────────────────────────────────────────────────────────────────


def _make_fake_controller(
    *,
    namespace: str = "sisyphus-runners",
    list_namespaced_pod: Any | None = None,
    list_namespace_sentinel: Any | None = None,
) -> MagicMock:
    """Build a fake RunnerController with a tracked core_v1 surface.

    `list_namespaced_pod` is a MagicMock the test can configure / inspect.
    `list_namespace_sentinel` is a separate MagicMock to assert NOT called.
    """
    controller = MagicMock()
    controller.namespace = namespace
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod = list_namespaced_pod or MagicMock(
        return_value=MagicMock(items=[])
    )
    core_v1.list_namespace = list_namespace_sentinel or MagicMock(
        side_effect=AssertionError(
            "/readyz MUST NOT call list_namespace (cluster-wide RBAC needed); "
            "use list_namespaced_pod scoped to controller.namespace instead."
        )
    )
    controller.core_v1 = core_v1
    return controller


def _make_passing_db_pool() -> MagicMock:
    """asyncpg.Pool stub whose acquire() yields a conn that returns 1
    for any SELECT — DB probe SHOULD pass."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock(return_value="SELECT 1")
    conn.fetchrow = AsyncMock(return_value=None)

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool.fetchval = AsyncMock(return_value=1)
    pool.execute = AsyncMock(return_value="SELECT 1")
    return pool


def _make_passing_bkd_httpx() -> MagicMock:
    """httpx.AsyncClient stub whose any HTTP call returns 200 — BKD probe
    SHOULD pass."""
    response = MagicMock()
    response.status_code = 200
    response.is_success = True
    response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value={"ok": True})
    response.text = "ok"

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.request = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    cls = MagicMock(return_value=client)
    return cls


@contextmanager
def _readyz_harness(
    *,
    controller: MagicMock | None | type(...) = ...,  # ... = use default fake
    raise_runtime_on_get_controller: bool = False,
):
    """Patch K8s controller + DB pool + httpx so /readyz is testable.

    - controller=...    → install a default fake via set_controller
    - controller=mock   → install mock via set_controller
    - controller=None   → skip set_controller (caller manages)
    - raise_runtime_on_get_controller=True → make get_controller() raise
                          RuntimeError("not init") regardless of singleton
                          (RZN-S3 simulation)
    """
    from orchestrator import k8s_runner
    from orchestrator.store import db

    # Pre-seed controller singleton (unless caller wants RuntimeError path)
    prev_controller = k8s_runner._controller
    if controller is ...:
        k8s_runner.set_controller(_make_fake_controller())
    elif controller is not None:
        k8s_runner.set_controller(controller)

    db_pool = _make_passing_db_pool()
    bkd_httpx_cls = _make_passing_bkd_httpx()

    patches = [
        patch.object(db, "get_pool", return_value=db_pool),
        patch("orchestrator.main.db.get_pool", return_value=db_pool),
        patch("orchestrator.main.httpx.AsyncClient", bkd_httpx_cls),
    ]

    if raise_runtime_on_get_controller:
        patches.append(
            patch.object(
                k8s_runner,
                "get_controller",
                side_effect=RuntimeError("not init"),
            )
        )
        patches.append(
            patch(
                "orchestrator.main.k8s_runner.get_controller",
                side_effect=RuntimeError("not init"),
            )
        )

    started = [p.start() for p in patches]
    try:
        yield
    finally:
        for p in patches:
            try:
                p.stop()
            except RuntimeError:
                pass
        k8s_runner.set_controller(prev_controller)


def _client() -> TestClient:
    from orchestrator.main import app

    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# RZN-S1: happy path — 200 + namespaced call with limit=1 + _request_timeout=2
# ─────────────────────────────────────────────────────────────────────────────


def test_RZN_S1_happy_path_returns_200_with_namespaced_pod_call() -> None:
    list_pod = MagicMock(return_value=MagicMock(items=[]))
    list_namespace_sentinel = MagicMock(
        side_effect=AssertionError(
            "/readyz MUST NOT call list_namespace per spec (cluster-wide RBAC denied)."
        )
    )
    controller = _make_fake_controller(
        namespace="sisyphus-runners",
        list_namespaced_pod=list_pod,
        list_namespace_sentinel=list_namespace_sentinel,
    )

    with _readyz_harness(controller=controller):
        resp = _client().get("/readyz")

    # Response contract
    assert resp.status_code == 200, (
        f"RZN-S1: expected 200 OK on happy path, got {resp.status_code}: {resp.text!r}"
    )
    body = resp.json()
    assert body == {"status": "ok"}, (
        f'RZN-S1: expected body {{"status": "ok"}}, got {body!r}'
    )

    # Call contract: list_namespaced_pod called exactly once
    assert list_pod.call_count == 1, (
        f"RZN-S1: expected list_namespaced_pod called exactly once, "
        f"got {list_pod.call_count} calls; call_args_list={list_pod.call_args_list!r}"
    )
    call = list_pod.call_args
    args = call.args
    kwargs = call.kwargs

    # Positional arg 0 (or kwarg `namespace`) MUST be "sisyphus-runners"
    actual_namespace = args[0] if args else kwargs.get("namespace")
    assert actual_namespace == "sisyphus-runners", (
        f"RZN-S1: list_namespaced_pod target namespace MUST be 'sisyphus-runners' "
        f"(controller.namespace), got {actual_namespace!r}; full call={call!r}"
    )

    # kwargs MUST contain limit=1
    assert kwargs.get("limit") == 1, (
        f"RZN-S1: list_namespaced_pod MUST be called with limit=1, "
        f"got kwargs={kwargs!r}"
    )

    # kwargs MUST contain _request_timeout=2
    assert kwargs.get("_request_timeout") == 2, (
        f"RZN-S1: list_namespaced_pod MUST be called with _request_timeout=2, "
        f"got kwargs={kwargs!r}"
    )

    # list_namespace MUST NOT be called (sentinel raises if it is)
    assert list_namespace_sentinel.call_count == 0, (
        "RZN-S1: list_namespace MUST NOT be called (needs cluster-wide RBAC); "
        "/readyz must use list_namespaced_pod instead."
    )


# ─────────────────────────────────────────────────────────────────────────────
# RZN-S2: list_namespaced_pod raises non-RuntimeError → 503, failed=["k8s"]
# ─────────────────────────────────────────────────────────────────────────────


class _FakeApiException(Exception):
    """Stand-in for kubernetes.client.exceptions.ApiException — non-RuntimeError."""

    def __init__(self, status: int, reason: str = "Forbidden"):
        super().__init__(f"{status} {reason}")
        self.status = status
        self.reason = reason


def test_RZN_S2_k8s_api_failure_returns_503_with_k8s_in_failed() -> None:
    boom = MagicMock(side_effect=_FakeApiException(403, "Forbidden"))
    controller = _make_fake_controller(
        namespace="sisyphus-runners", list_namespaced_pod=boom
    )

    with _readyz_harness(controller=controller):
        resp = _client().get("/readyz")

    assert resp.status_code == 503, (
        f"RZN-S2: K8s probe failure MUST yield HTTP 503, got {resp.status_code}: {resp.text!r}"
    )
    body = resp.json()
    assert isinstance(body, dict), f"RZN-S2: body must be JSON object, got {body!r}"
    assert body.get("status") == "not_ready", (
        f'RZN-S2: body.status MUST be "not_ready", got {body!r}'
    )
    failed = body.get("failed")
    assert isinstance(failed, list), (
        f'RZN-S2: body.failed MUST be a list, got {failed!r}'
    )
    assert "k8s" in failed, (
        f'RZN-S2: body.failed MUST contain "k8s", got {failed!r}'
    )
    assert "db" not in failed, (
        f'RZN-S2: body.failed MUST NOT contain "db" when only K8s fails, got {failed!r}'
    )
    assert "bkd" not in failed, (
        f'RZN-S2: body.failed MUST NOT contain "bkd" when only K8s fails, got {failed!r}'
    )


def test_RZN_S2_k8s_api_failure_with_timeout_also_503() -> None:
    """非 RuntimeError 包含网络超时一类 —— 同样 503 + k8s in failed。"""
    boom = MagicMock(side_effect=TimeoutError("k8s api timeout"))
    controller = _make_fake_controller(
        namespace="sisyphus-runners", list_namespaced_pod=boom
    )

    with _readyz_harness(controller=controller):
        resp = _client().get("/readyz")

    assert resp.status_code == 503, (
        f"RZN-S2 (timeout variant): expected 503, got {resp.status_code}: {resp.text!r}"
    )
    failed = resp.json().get("failed", [])
    assert "k8s" in failed, (
        f'RZN-S2 (timeout variant): "k8s" must be in failed, got {failed!r}'
    )


# ─────────────────────────────────────────────────────────────────────────────
# RZN-S3: get_controller raises RuntimeError → 200, NOT in failed (skip)
# ─────────────────────────────────────────────────────────────────────────────


def test_RZN_S3_controller_not_initialized_skipped_returns_200() -> None:
    with _readyz_harness(
        controller=None, raise_runtime_on_get_controller=True
    ):
        resp = _client().get("/readyz")

    assert resp.status_code == 200, (
        f"RZN-S3: RuntimeError from get_controller MUST be treated as skip "
        f"(not fail) → 200, got {resp.status_code}: {resp.text!r}"
    )
    body = resp.json()
    assert body == {"status": "ok"}, (
        f'RZN-S3: expected body {{"status": "ok"}}, got {body!r}'
    )

    # Even if body shape evolves to include `failed` on success, "k8s" must
    # not appear there.
    failed = body.get("failed", []) if isinstance(body, dict) else []
    assert "k8s" not in failed, (
        f'RZN-S3: body.failed MUST NOT contain "k8s" when controller is '
        f"uninitialized (RuntimeError = skip), got failed={failed!r}"
    )
