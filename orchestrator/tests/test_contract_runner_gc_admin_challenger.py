"""Contract tests for runner-gc-admin (REQ-430).

Black-box challenger. Does NOT read runner_gc.py or admin.py. Derived from:
  openspec/changes/REQ-430/specs/runner-gc-admin/spec.md

Scenarios:
  RGA-S1  POST /admin/runner-gc with valid token + controller → 200 with full GC result
  RGA-S2  POST /admin/runner-gc with valid token + no controller → 200 with "skipped"
  RGA-S3  POST /admin/runner-gc without token → 401
  RGA-S4  GET /admin/runner-gc/status before any GC → {"last": null}
  RGA-S5  GET /admin/runner-gc/status after GC → {"last": {...ran_at, cleaned_pods, ...}}

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

_TOKEN = "test-webhook-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}

_FULL_GC_RESULT = {
    "cleaned_pods": ["runner-req-42"],
    "cleaned_pvcs": [],
    "pod_kept": 2,
    "pvc_kept": 3,
    "disk_pressure": False,
    "ran_at": "2026-01-01T00:00:00+00:00",
}

_SKIPPED_GC_RESULT = {
    "skipped": "no k8s controller",
    "ran_at": "2026-01-01T00:00:00+00:00",
}


# ─── RGA-S1 ──────────────────────────────────────────────────────────────────


async def test_rga_s1_post_runner_gc_returns_full_result(monkeypatch):
    """RGA-S1: POST /admin/runner-gc with valid token and K8s controller available
    MUST return 200 with cleaned_pods, cleaned_pvcs, pod_kept, pvc_kept,
    disk_pressure, and ran_at (non-empty string).
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import runner_gc as runner_gc_mod
    from orchestrator.main import app

    async def _fake_gc_once():
        return _FULL_GC_RESULT.copy()

    monkeypatch.setattr(runner_gc_mod, "gc_once", _fake_gc_once)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/runner-gc", headers=_AUTH)

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    for field in ("cleaned_pods", "cleaned_pvcs", "pod_kept", "pvc_kept", "disk_pressure", "ran_at"):
        assert field in body, f"response missing required field '{field}': {body}"
    assert isinstance(body["cleaned_pods"], list), f"cleaned_pods must be a list: {body}"
    assert isinstance(body["cleaned_pvcs"], list), f"cleaned_pvcs must be a list: {body}"
    assert isinstance(body["pod_kept"], int), f"pod_kept must be int: {body}"
    assert isinstance(body["pvc_kept"], int), f"pvc_kept must be int: {body}"
    assert isinstance(body["disk_pressure"], bool), f"disk_pressure must be bool: {body}"
    assert body["ran_at"], f"ran_at must be a non-empty string: {body}"


# ─── RGA-S2 ──────────────────────────────────────────────────────────────────


async def test_rga_s2_post_runner_gc_no_controller_returns_skipped(monkeypatch):
    """RGA-S2: POST /admin/runner-gc with valid token but no K8s runner controller
    MUST return 200 with JSON containing key "skipped".
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import runner_gc as runner_gc_mod
    from orchestrator.main import app

    async def _fake_gc_once():
        return _SKIPPED_GC_RESULT.copy()

    monkeypatch.setattr(runner_gc_mod, "gc_once", _fake_gc_once)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/runner-gc", headers=_AUTH)

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "skipped" in body, (
        f"response must contain 'skipped' key when no controller available: {body}"
    )


# ─── RGA-S3 ──────────────────────────────────────────────────────────────────


async def test_rga_s3_post_runner_gc_no_auth_returns_401():
    """RGA-S3: POST /admin/runner-gc without Authorization header MUST return 401."""
    from httpx import ASGITransport, AsyncClient

    from orchestrator.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/runner-gc")

    assert resp.status_code == 401, f"expected 401, got {resp.status_code}: {resp.text}"


# ─── RGA-S4 ──────────────────────────────────────────────────────────────────


async def test_rga_s4_status_before_any_gc_returns_null(monkeypatch):
    """RGA-S4: GET /admin/runner-gc/status before any GC pass
    MUST return 200 with JSON {"last": null}.
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import runner_gc as runner_gc_mod
    from orchestrator.main import app

    monkeypatch.setattr(runner_gc_mod, "get_last_result", lambda: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/runner-gc/status")

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "last" in body, f"response must have 'last' key: {body}"
    assert body["last"] is None, f"'last' must be null before any GC pass: {body}"


# ─── RGA-S5 ──────────────────────────────────────────────────────────────────


async def test_rga_s5_status_after_gc_returns_last_result(monkeypatch):
    """RGA-S5: GET /admin/runner-gc/status after at least one GC pass (timer or admin trigger)
    MUST return 200 with JSON containing last.ran_at and last.cleaned_pods.
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import runner_gc as runner_gc_mod
    from orchestrator.main import app

    _stored = _FULL_GC_RESULT.copy()
    monkeypatch.setattr(runner_gc_mod, "get_last_result", lambda: _stored)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/admin/runner-gc/status")

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "last" in body, f"response must have 'last' key: {body}"
    last = body["last"]
    assert last is not None, f"'last' must not be null after a GC pass: {body}"
    assert "ran_at" in last, f"last must contain 'ran_at': {last}"
    assert last["ran_at"], f"last.ran_at must be a non-empty string: {last}"
    assert "cleaned_pods" in last, f"last must contain 'cleaned_pods': {last}"
