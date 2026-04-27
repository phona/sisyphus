"""Challenger contract tests for REQ-428: verifier infra-flake auto-retry.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-428/specs/verifier-infra-flake-retry/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  VFR-S1  (REVIEW_RUNNING, VERIFY_INFRA_RETRY) → apply_verify_infra_retry dispatch,
           engine CASes self-loop to REVIEW_RUNNING
  VFR-S2  apply_verify_infra_retry, count=0, verifier_stage=staging_test →
           infra_retry_count increments to 1, CAS to STAGING_TEST_RUNNING, create_staging_test called
  VFR-S3  apply_verify_infra_retry, count=2 (= verifier_infra_retry_cap=2) →
           returns emit=verify.escalate, reason=infra-retry-cap
  VFR-S4  apply_verify_infra_retry, verifier_stage=analyze (not in _RETRY_ROUTING) →
           returns emit=verify.escalate, logs stage_not_retryable
  VFR-S5  validate_decision: action=retry + fixer=dev (non-null) → MUST fail validation
  VFR-S6  decision_to_event: action=retry → Event.VERIFY_INFRA_RETRY
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Shared helpers ───────────────────────────────────────────────────────────

_POOL = object()
_REQ_ID = "REQ-428"
_PROJECT = "proj-vfr"


class _Body:
    issueId = "bkd-vfr-test"
    projectId = _PROJECT


def _patch_engine_io(monkeypatch) -> AsyncMock:
    """Patch engine external I/O (DB + stage_runs). Returns cas_mock."""
    from orchestrator import engine

    cas = AsyncMock(return_value=True)
    monkeypatch.setattr(engine.req_state, "cas_transition", cas)
    monkeypatch.setattr(engine.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(engine.stage_runs, "close_latest_stage_run", AsyncMock())
    monkeypatch.setattr(engine.stage_runs, "insert_stage_run", AsyncMock())
    monkeypatch.setattr(engine.obs, "record_event", AsyncMock())
    return cas


@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot and restore REGISTRY around each test."""
    from orchestrator.actions import ACTION_META, REGISTRY

    snap_reg = dict(REGISTRY)
    snap_meta = dict(ACTION_META)
    yield
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(snap_reg)
    ACTION_META.update(snap_meta)


async def _engine_step(**overrides):
    from orchestrator.engine import step
    from orchestrator.state import Event, ReqState

    defaults: dict = dict(
        pool=_POOL,
        body=_Body(),
        req_id=_REQ_ID,
        project_id=_PROJECT,
        tags=[_REQ_ID],
        cur_state=ReqState.REVIEW_RUNNING,
        ctx={},
        event=Event.VERIFY_INFRA_RETRY,
        depth=0,
    )
    defaults.update(overrides)
    return await step(**defaults)


def _make_settings(verifier_infra_retry_cap: int = 2) -> Any:
    s = MagicMock()
    s.verifier_infra_retry_cap = verifier_infra_retry_cap
    return s


def _make_body_obj(project_id: str = _PROJECT) -> Any:
    b = MagicMock()
    b.projectId = project_id
    return b


def _action_patches(settings, mock_req_state, mock_stage_runs):
    """Return patch contexts for apply_verify_infra_retry tests."""
    return [
        patch("orchestrator.actions._verifier.settings", settings),
        patch("orchestrator.actions._verifier.req_state", mock_req_state),
        patch("orchestrator.actions._verifier.stage_runs", mock_stage_runs),
        patch("orchestrator.store.db.get_pool", return_value=AsyncMock()),
    ]


# ─── VFR-S1: engine routes VERIFY_INFRA_RETRY to apply_verify_infra_retry ────


@pytest.mark.asyncio
async def test_vfr_s1_review_running_verify_infra_retry_dispatches_action_and_self_loops(
    monkeypatch,
) -> None:
    """VFR-S1: (REVIEW_RUNNING, VERIFY_INFRA_RETRY) → apply_verify_infra_retry.
    Engine MUST dispatch apply_verify_infra_retry and CAS to REVIEW_RUNNING (self-loop).
    The action itself internally CASes the real state advance; engine stays at REVIEW_RUNNING."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_engine_io(monkeypatch)
    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("apply_verify_infra_retry")
        return {}

    REGISTRY["apply_verify_infra_retry"] = _stub

    result = await _engine_step(
        cur_state=ReqState.REVIEW_RUNNING,
        event=Event.VERIFY_INFRA_RETRY,
        tags=["verifier", _REQ_ID, "verify:staging_test"],
        ctx={"verifier_stage": "staging_test"},
    )

    assert result.get("action") == "apply_verify_infra_retry", (
        f"VFR-S1: action MUST be 'apply_verify_infra_retry'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.REVIEW_RUNNING.value, (
        f"VFR-S1: next_state MUST remain 'review-running' (self-loop); got {result!r}"
    )
    assert len(calls) == 1, (
        f"VFR-S1: apply_verify_infra_retry MUST be awaited exactly once; called {len(calls)}"
    )
    assert cas.called, "VFR-S1: engine MUST call cas_transition"
    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.REVIEW_RUNNING, (
        f"VFR-S1: engine CAS MUST target REVIEW_RUNNING self-loop; got {cas_next!r}"
    )


# ─── VFR-S2: apply_verify_infra_retry below cap ───────────────────────────────


@pytest.mark.asyncio
async def test_vfr_s2_below_cap_increments_count_cas_to_stage_running_and_calls_create(
) -> None:
    """VFR-S2: count=0, verifier_stage=staging_test, cap=2 →
    infra_retry_count increments to 1, CAS to STAGING_TEST_RUNNING, create_staging_test invoked."""
    from orchestrator.actions import REGISTRY
    from orchestrator.actions._verifier import apply_verify_infra_retry
    from orchestrator.state import Event, ReqState

    settings = _make_settings(verifier_infra_retry_cap=2)
    mock_req_state = AsyncMock()
    mock_stage_runs = AsyncMock()

    written_ctx: dict = {}

    async def _capture_ctx(pool, req_id, updates):
        written_ctx.update(updates)

    mock_req_state.update_context.side_effect = _capture_ctx
    mock_req_state.cas_transition = AsyncMock(return_value=True)

    create_calls: list[str] = []

    async def _fake_create(**_kw):
        create_calls.append("create_staging_test")
        return {"created": True}

    REGISTRY["create_staging_test"] = _fake_create

    p = _action_patches(settings, mock_req_state, mock_stage_runs)
    with p[0], p[1], p[2], p[3]:
        result = await apply_verify_infra_retry(
            body=_make_body_obj(),
            req_id=_REQ_ID,
            tags=["verifier", _REQ_ID, "verify:staging_test"],
            ctx={"verifier_stage": "staging_test", "infra_retry_count": 0},
        )

    assert written_ctx.get("infra_retry_count") == 1, (
        f"VFR-S2: infra_retry_count MUST be incremented to 1; "
        f"update_context got: {written_ctx!r}"
    )

    cas_calls = mock_req_state.cas_transition.call_args_list
    assert len(cas_calls) >= 1, (
        f"VFR-S2: req_state.cas_transition MUST be called (action-internal CAS); "
        f"called {len(cas_calls)} time(s)"
    )
    cas_target = cas_calls[0].args[3]
    assert cas_target == ReqState.STAGING_TEST_RUNNING, (
        f"VFR-S2: action CAS MUST target STAGING_TEST_RUNNING; got {cas_target!r}"
    )

    assert len(create_calls) == 1, (
        f"VFR-S2: create_staging_test MUST be called exactly once; called {len(create_calls)}"
    )


# ─── VFR-S3: apply_verify_infra_retry at cap ─────────────────────────────────


@pytest.mark.asyncio
async def test_vfr_s3_at_cap_emits_verify_escalate_with_infra_retry_cap_reason() -> None:
    """VFR-S3: count=2 (= verifier_infra_retry_cap=2) →
    MUST return emit=verify.escalate, reason=infra-retry-cap; MUST NOT call create action."""
    from orchestrator.actions import REGISTRY
    from orchestrator.actions._verifier import apply_verify_infra_retry
    from orchestrator.state import Event

    settings = _make_settings(verifier_infra_retry_cap=2)
    mock_req_state = AsyncMock()
    mock_stage_runs = AsyncMock()

    written_ctx: dict = {}

    async def _capture_ctx(pool, req_id, updates):
        written_ctx.update(updates)

    mock_req_state.update_context.side_effect = _capture_ctx

    create_calls: list[str] = []

    async def _fake_create(**_kw):
        create_calls.append("must-not-be-called")
        return {}

    REGISTRY["create_staging_test"] = _fake_create

    p = _action_patches(settings, mock_req_state, mock_stage_runs)
    with p[0], p[1], p[2], p[3]:
        result = await apply_verify_infra_retry(
            body=_make_body_obj(),
            req_id=_REQ_ID,
            tags=["verifier", _REQ_ID, "verify:staging_test"],
            ctx={"verifier_stage": "staging_test", "infra_retry_count": 2},
        )

    assert isinstance(result, dict), (
        f"VFR-S3: apply_verify_infra_retry MUST return a dict at cap; got {type(result)}"
    )
    assert result.get("emit") == Event.VERIFY_ESCALATE.value, (
        f"VFR-S3: emit MUST be '{Event.VERIFY_ESCALATE.value}' when cap hit; got {result!r}"
    )
    assert result.get("reason") == "infra-retry-cap", (
        f"VFR-S3: reason MUST be 'infra-retry-cap'; got {result!r}"
    )
    assert len(create_calls) == 0, (
        f"VFR-S3: create action MUST NOT be called when retry cap is hit; "
        f"called {len(create_calls)} time(s)"
    )
    assert written_ctx.get("escalated_reason") == "infra-retry-cap", (
        f"VFR-S3: ctx.escalated_reason MUST be written as 'infra-retry-cap'; "
        f"update_context received: {written_ctx!r}"
    )


# ─── VFR-S4: non-retryable stage escalates with stage_not_retryable ──────────


@pytest.mark.asyncio
async def test_vfr_s4_non_retryable_stage_emits_verify_escalate() -> None:
    """VFR-S4: verifier_stage=analyze (not in _RETRY_ROUTING) →
    MUST return emit=verify.escalate; MUST NOT CAS or call create action."""
    from orchestrator.actions._verifier import apply_verify_infra_retry
    from orchestrator.state import Event

    settings = _make_settings(verifier_infra_retry_cap=2)
    mock_req_state = AsyncMock()
    mock_stage_runs = AsyncMock()

    p = _action_patches(settings, mock_req_state, mock_stage_runs)
    with p[0], p[1], p[2], p[3]:
        result = await apply_verify_infra_retry(
            body=_make_body_obj(),
            req_id=_REQ_ID,
            tags=["verifier", _REQ_ID, "verify:analyze"],
            ctx={"verifier_stage": "analyze", "infra_retry_count": 0},
        )

    assert isinstance(result, dict), (
        f"VFR-S4: MUST return a dict for non-retryable stage; got {type(result)}"
    )
    assert result.get("emit") == Event.VERIFY_ESCALATE.value, (
        f"VFR-S4: emit MUST be '{Event.VERIFY_ESCALATE.value}' for non-retryable stage; "
        f"got {result!r}"
    )
    cas_calls = mock_req_state.cas_transition.call_args_list
    assert len(cas_calls) == 0, (
        f"VFR-S4: cas_transition MUST NOT be called for non-retryable stage; "
        f"called {len(cas_calls)} time(s)"
    )


# ─── VFR-S5: validate_decision rejects retry + non-null fixer ────────────────


def test_vfr_s5_validate_decision_retry_with_nonnull_fixer_is_invalid() -> None:
    """VFR-S5: decision {action=retry, fixer=dev} MUST fail validate_decision.
    retry action requires fixer=null — a non-null fixer is a schema violation."""
    from orchestrator.router import validate_decision

    invalid_decision = {
        "action": "retry",
        "fixer": "dev",
        "scope": None,
        "reason": "infra flaky: kubectl exec channel",
        "confidence": "high",
    }
    ok, msg = validate_decision(invalid_decision)

    assert ok is False, (
        f"VFR-S5: validate_decision MUST return False for action=retry + fixer=dev; "
        f"got ok={ok!r}, msg={msg!r}"
    )


def test_vfr_s5_validate_decision_retry_with_null_fixer_is_valid() -> None:
    """VFR-S5 (positive): decision {action=retry, fixer=null} MUST pass validate_decision."""
    from orchestrator.router import validate_decision

    valid_decision = {
        "action": "retry",
        "fixer": None,
        "scope": None,
        "reason": "infra flaky: kubectl exec channel race",
        "confidence": "high",
    }
    ok, _ = validate_decision(valid_decision)

    assert ok is True, (
        f"VFR-S5: validate_decision MUST accept action=retry with fixer=null; got ok={ok!r}"
    )


# ─── VFR-S6: decision_to_event maps action=retry to VERIFY_INFRA_RETRY ───────


def test_vfr_s6_decision_to_event_retry_maps_to_verify_infra_retry() -> None:
    """VFR-S6: decision_to_event({action: retry}) MUST return Event.VERIFY_INFRA_RETRY.
    This is the router bridge between verifier JSON output and the state machine event."""
    from orchestrator.router import decision_to_event
    from orchestrator.state import Event

    result = decision_to_event({"action": "retry"})

    assert result == Event.VERIFY_INFRA_RETRY, (
        f"VFR-S6: decision_to_event(action=retry) MUST return Event.VERIFY_INFRA_RETRY; "
        f"got {result!r}"
    )
