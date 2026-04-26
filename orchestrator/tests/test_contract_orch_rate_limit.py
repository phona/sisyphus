"""Challenger contract tests: in-flight REQ cap + disk pressure admission gate.

REQ-orch-rate-limit-1777202974. Black-box challenger.

Derived exclusively from:
  openspec/changes/REQ-orch-rate-limit-1777202974/specs/orch-rate-limit/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.

Scenarios:
  ORCH-RATE-S1  cap=0 → always admit regardless of in-flight count
  ORCH-RATE-S2  count=9 < cap=10 → admit=True, reason=None
  ORCH-RATE-S3  count=10 >= cap=10 → admit=False, reason contains 'inflight-cap-exceeded'
  ORCH-RATE-S4  disk=0.50 < threshold=0.75 → admit=True
  ORCH-RATE-S5  disk=0.80 >= threshold=0.75 → admit=False, reason contains 'disk-pressure'
  ORCH-RATE-S6  get_controller() raises RuntimeError → fail open (admit=True)
  ORCH-RATE-C1  start_intake returns verify.escalate + ctx.escalated_reason on rejection
  ORCH-RATE-C2  start_analyze returns verify.escalate + ctx.escalated_reason on rejection
  ORCH-RATE-C3  start_analyze_with_finalized_intent does NOT reference check_admission
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


# ─── fake pool ───────────────────────────────────────────────────────────────


class _AnyKeyRow:
    """asyncpg Record fake: returns the same value for any int or str key."""

    def __init__(self, value: int):
        self._v = value

    def __getitem__(self, key):
        return self._v

    def get(self, key, default=None):
        return self._v


class _CountPool:
    """asyncpg pool fake: fetchrow returns _AnyKeyRow(count) for any SQL."""

    def __init__(self, count: int = 0):
        self._count = count

    async def fetchrow(self, sql: str, *args):
        return _AnyKeyRow(self._count)

    async def execute(self, sql: str, *args):
        pass

    async def fetch(self, sql: str, *args):
        return []


# ─── common patch helpers ─────────────────────────────────────────────────────


def _patch_settings(monkeypatch, cap: int = 10, disk_threshold: float = 0.75):
    from orchestrator.config import settings
    monkeypatch.setattr(settings, "inflight_req_cap", cap)
    monkeypatch.setattr(settings, "admission_disk_pressure_threshold", disk_threshold)


def _patch_controller(monkeypatch, ratio: float | None = 0.0, raise_exc: Exception | None = None):
    import orchestrator.k8s_runner as krm

    if raise_exc is not None:
        def _raise_ctrl():
            raise raise_exc
        monkeypatch.setattr(krm, "get_controller", _raise_ctrl)
    elif ratio is None:
        monkeypatch.setattr(krm, "get_controller", lambda: None)
    else:
        ctrl = MagicMock()
        ctrl.node_disk_usage_ratio = AsyncMock(return_value=ratio)
        monkeypatch.setattr(krm, "get_controller", lambda: ctrl)


# ─── ORCH-RATE-S1: cap=0 disables gate ───────────────────────────────────────


async def test_s1_cap_zero_always_admits(monkeypatch):
    """ORCH-RATE-S1: cap=0 MUST disable in-flight gate; admit=True even with 50 active REQs."""
    _patch_settings(monkeypatch, cap=0)
    _patch_controller(monkeypatch, ratio=0.0)
    from orchestrator.admission import check_admission

    result = await check_admission(_CountPool(count=50), req_id="REQ-new")
    assert result.admit is True, (
        f"ORCH-RATE-S1: cap=0 MUST always admit regardless of count. "
        f"Got admit={result.admit!r}"
    )


# ─── ORCH-RATE-S2: count under cap → admit ───────────────────────────────────


async def test_s2_count_under_cap_admits(monkeypatch):
    """ORCH-RATE-S2: count=9 < cap=10, disk fine → admit=True, reason=None."""
    _patch_settings(monkeypatch, cap=10)
    _patch_controller(monkeypatch, ratio=0.0)
    from orchestrator.admission import check_admission

    result = await check_admission(_CountPool(count=9), req_id="REQ-new")
    assert result.admit is True, (
        f"ORCH-RATE-S2: count=9 < cap=10 MUST admit. Got admit={result.admit!r}"
    )
    assert result.reason is None, (
        f"ORCH-RATE-S2: reason MUST be None on admit. Got reason={result.reason!r}"
    )


# ─── ORCH-RATE-S3: count at cap → reject ─────────────────────────────────────


async def test_s3_count_at_cap_rejects(monkeypatch):
    """ORCH-RATE-S3: count=10 >= cap=10 → admit=False, reason contains 'inflight-cap-exceeded'."""
    _patch_settings(monkeypatch, cap=10)
    _patch_controller(monkeypatch, ratio=0.0)
    from orchestrator.admission import check_admission

    result = await check_admission(_CountPool(count=10), req_id="REQ-new")
    assert result.admit is False, (
        f"ORCH-RATE-S3: count=10 >= cap=10 MUST reject. Got admit={result.admit!r}"
    )
    assert result.reason is not None, (
        "ORCH-RATE-S3: reason MUST be set (non-None) on cap rejection"
    )
    assert "inflight-cap-exceeded" in result.reason, (
        f"ORCH-RATE-S3: reason MUST contain 'inflight-cap-exceeded'. Got: {result.reason!r}"
    )


# ─── ORCH-RATE-S4: disk under threshold → admit ──────────────────────────────


async def test_s4_disk_under_threshold_admits(monkeypatch):
    """ORCH-RATE-S4: disk=0.50 < threshold=0.75, count well below cap → admit=True."""
    _patch_settings(monkeypatch, cap=10, disk_threshold=0.75)
    _patch_controller(monkeypatch, ratio=0.50)
    from orchestrator.admission import check_admission

    result = await check_admission(_CountPool(count=0), req_id="REQ-new")
    assert result.admit is True, (
        f"ORCH-RATE-S4: disk=0.50 < 0.75 MUST admit. Got admit={result.admit!r}"
    )


# ─── ORCH-RATE-S5: disk above threshold → reject ─────────────────────────────


async def test_s5_disk_above_threshold_rejects(monkeypatch):
    """ORCH-RATE-S5: disk=0.80 >= threshold=0.75 → admit=False, reason contains 'disk-pressure'."""
    _patch_settings(monkeypatch, cap=10, disk_threshold=0.75)
    _patch_controller(monkeypatch, ratio=0.80)
    from orchestrator.admission import check_admission

    result = await check_admission(_CountPool(count=0), req_id="REQ-new")
    assert result.admit is False, (
        f"ORCH-RATE-S5: disk=0.80 >= 0.75 MUST reject. Got admit={result.admit!r}"
    )
    assert result.reason is not None, (
        "ORCH-RATE-S5: reason MUST be set (non-None) on disk-pressure rejection"
    )
    assert "disk-pressure" in result.reason, (
        f"ORCH-RATE-S5: reason MUST contain 'disk-pressure'. Got: {result.reason!r}"
    )


# ─── ORCH-RATE-S6: missing runner controller → fail open ─────────────────────


async def test_s6_controller_raises_fails_open(monkeypatch):
    """ORCH-RATE-S6: get_controller() raises RuntimeError → admit=True (fail open, no exception)."""
    _patch_settings(monkeypatch, cap=10, disk_threshold=0.75)
    _patch_controller(monkeypatch, raise_exc=RuntimeError("no K8s in dev env"))
    from orchestrator.admission import check_admission

    caught: Exception | None = None
    result = None
    try:
        result = await check_admission(_CountPool(count=0), req_id="REQ-new")
    except Exception as exc:
        caught = exc

    assert caught is None, (
        f"ORCH-RATE-S6: check_admission MUST NOT raise when controller is absent. "
        f"Got: {type(caught)}: {caught}"
    )
    assert result is not None and result.admit is True, (
        f"ORCH-RATE-S6: MUST fail open (admit=True). Got result={result!r}"
    )


# ─── ORCH-RATE-C1: start_intake emits verify.escalate on rejection ───────────


async def test_c1_start_intake_emits_escalate_on_rejection(monkeypatch):
    """ORCH-RATE-C1: when check_admission rejects, start_intake MUST:
    - return dict with emit='verify.escalate'
    - record escalated_reason containing 'inflight-cap-exceeded' (via ctx mutation
      or update_context call)
    """
    from collections import namedtuple

    import orchestrator.admission as adm_mod
    from orchestrator.store import db as db_mod
    from orchestrator.store import req_state as rs_mod

    Rejected = namedtuple("Rejected", ["admit", "reason"])
    rejected = Rejected(admit=False, reason="rate-limit:inflight-cap-exceeded")
    mock_gate = AsyncMock(return_value=rejected)

    monkeypatch.setattr(adm_mod, "check_admission", mock_gate)
    monkeypatch.setattr(db_mod, "get_pool", lambda: _CountPool(0))

    # Capture any update_context calls (action may write escalated_reason this way)
    ctx_patches: list[dict] = []

    async def _capture_update(pool, req_id, patch):
        ctx_patches.append(dict(patch))

    monkeypatch.setattr(rs_mod, "update_context", _capture_update, raising=False)

    from orchestrator.actions import start_intake as mod
    monkeypatch.setattr(mod, "check_admission", mock_gate, raising=False)

    bkd = AsyncMock()
    bkd.update_issue = AsyncMock()
    bkd.get_issue = AsyncMock(return_value=MagicMock(tags=[], title="T"))
    bkd.merge_tags_and_update = AsyncMock()

    @asynccontextmanager
    async def _bkd(*_, **__):
        yield bkd

    monkeypatch.setattr(mod, "BKDClient", _bkd, raising=False)

    ctx: dict = {}
    body = type("B", (), {
        "issueId": "intake-1", "projectId": "p",
        "event": "session.completed", "title": "T",
        "tags": ["REQ-new", "intent:intake"], "issueNumber": 1,
    })()

    result = await mod.start_intake(
        body=body, req_id="REQ-new",
        tags=["REQ-new", "intent:intake"], ctx=ctx,
    )

    assert isinstance(result, dict), (
        f"ORCH-RATE-C1: start_intake MUST return dict. Got {type(result)}"
    )
    assert result.get("emit") == "verify.escalate", (
        f"ORCH-RATE-C1: emit MUST be 'verify.escalate'. Got result={result!r}"
    )
    # Check escalated_reason in ctx dict OR in update_context patches
    all_reasons = (
        [ctx.get("escalated_reason", "")]
        + [p.get("escalated_reason", "") for p in ctx_patches]
    )
    assert any("inflight-cap-exceeded" in (r or "") for r in all_reasons), (
        f"ORCH-RATE-C1: escalated_reason MUST contain 'inflight-cap-exceeded'. "
        f"ctx={ctx!r}, update_context patches={ctx_patches}"
    )


# ─── ORCH-RATE-C2: start_analyze emits verify.escalate on rejection ──────────


async def test_c2_start_analyze_emits_escalate_on_rejection(monkeypatch):
    """ORCH-RATE-C2: when check_admission rejects, start_analyze MUST:
    - return dict with emit='verify.escalate'
    - record escalated_reason containing 'inflight-cap-exceeded' (via ctx mutation
      or update_context call)
    """
    from collections import namedtuple

    import orchestrator.admission as adm_mod
    from orchestrator.store import db as db_mod
    from orchestrator.store import req_state as rs_mod

    Rejected = namedtuple("Rejected", ["admit", "reason"])
    rejected = Rejected(admit=False, reason="rate-limit:inflight-cap-exceeded")
    mock_gate = AsyncMock(return_value=rejected)

    monkeypatch.setattr(adm_mod, "check_admission", mock_gate)
    monkeypatch.setattr(db_mod, "get_pool", lambda: _CountPool(0))

    ctx_patches: list[dict] = []

    async def _capture_update(pool, req_id, patch):
        ctx_patches.append(dict(patch))

    monkeypatch.setattr(rs_mod, "update_context", _capture_update, raising=False)

    from orchestrator.actions import start_analyze as mod
    monkeypatch.setattr(mod, "check_admission", mock_gate, raising=False)

    bkd = AsyncMock()
    bkd.update_issue = AsyncMock()
    bkd.get_issue = AsyncMock(return_value=MagicMock(tags=[], title="T"))
    bkd.merge_tags_and_update = AsyncMock()

    @asynccontextmanager
    async def _bkd(*_, **__):
        yield bkd

    monkeypatch.setattr(mod, "BKDClient", _bkd, raising=False)

    ctx: dict = {}
    body = type("B", (), {
        "issueId": "analyze-2", "projectId": "p",
        "event": "session.completed", "title": "T",
        "tags": ["REQ-x", "intent:analyze"], "issueNumber": 2,
    })()

    result = await mod.start_analyze(
        body=body, req_id="REQ-x",
        tags=["REQ-x", "intent:analyze"], ctx=ctx,
    )

    assert isinstance(result, dict), (
        f"ORCH-RATE-C2: start_analyze MUST return dict. Got {type(result)}"
    )
    assert result.get("emit") == "verify.escalate", (
        f"ORCH-RATE-C2: emit MUST be 'verify.escalate'. Got result={result!r}"
    )
    all_reasons = (
        [ctx.get("escalated_reason", "")]
        + [p.get("escalated_reason", "") for p in ctx_patches]
    )
    assert any("inflight-cap-exceeded" in (r or "") for r in all_reasons), (
        f"ORCH-RATE-C2: escalated_reason MUST contain 'inflight-cap-exceeded'. "
        f"ctx={ctx!r}, update_context patches={ctx_patches}"
    )


# ─── ORCH-RATE-C3: start_analyze_with_finalized_intent exempt from gate ──────


def test_c3_start_analyze_with_finalized_intent_not_gated():
    """ORCH-RATE-C3: start_analyze_with_finalized_intent MUST NOT invoke the admission gate.
    The spec explicitly exempts continuation actions from check_admission.
    Verified by source inspection: the action's source file must not reference check_admission.
    """
    src_actions = Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "actions"
    candidates = (
        list(src_actions.glob("*finalized_intent*"))
        + list(src_actions.glob("*start_analyze_with*"))
    )
    assert candidates, (
        "ORCH-RATE-C3: expected source file for start_analyze_with_finalized_intent "
        f"under {src_actions}/ — not found."
    )
    for f in candidates:
        text = f.read_text(encoding="utf-8")
        assert "check_admission" not in text, (
            f"ORCH-RATE-C3: {f.name} MUST NOT reference check_admission "
            "(spec: continuation action is exempt from the admission gate per spec.md). "
            "Found 'check_admission' in file."
        )
