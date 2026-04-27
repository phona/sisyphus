"""Challenger contract tests for REQ-engine-adversarial-tests-v2-1777256408.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-engine-adversarial-tests-v2-1777256408/specs/engine-adversarial-tests/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  EAT-S1   handler returns unknown emit string → logged+dropped, no chained key
  EAT-S2   handler returns None → treated as empty dict, result["result"]=={}
  EAT-S3   handler returns list → treated as empty dict, no chained key
  EAT-S4   req_state.get returns None mid-chain → early return base_result, no chained
  EAT-S5   chained emit hits illegal transition → base_result["chained"].action=="skip"
  EAT-S6   action missing from REGISTRY → returns error dict, CAS committed to next_state
  EAT-S7   ESCALATED+VERIFY_ESCALATE self-loop → no-op, cleanup_runner not triggered
  EAT-S8   stage_runs raises → engine still advances (best-effort write)
  EAT-S9   body without issueId attribute → no AttributeError, normal result
  EAT-S10a depth=12 → handler still dispatched normally
  EAT-S10b depth=13 → recursion guard fires immediately, handler not called
  EAT-S11  SESSION_FAILED on DONE → skip, escalate not invoked
  EAT-S12  DONE terminal skips every Event enum member

Module contract under test: orchestrator.engine.step
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

# ─── Shared helpers ───────────────────────────────────────────────────────────


class _Body:
    issueId = "bkd-eat-test"
    projectId = "proj-eat"


class _NoAttrBody:
    """Body with no attributes at all (EAT-S9)."""


class _FakeRow:
    def __init__(self, state, context=None):
        self.state = state
        self.context = context or {}


_POOL = object()  # sentinel; all DB calls are patched away
_REQ_ID = "REQ-engine-adversarial-tests-v2-1777256408"
_PROJECT = "proj-eat"
_TAGS: list[str] = [_REQ_ID]


def _patch_io(
    monkeypatch,
    *,
    get_side_effects=None,
    cas_return: bool = True,
    stage_raises: Exception | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Patch all engine external I/O. Returns (cas_mock, get_mock)."""
    from orchestrator import engine

    cas = AsyncMock(return_value=cas_return)
    monkeypatch.setattr(engine.req_state, "cas_transition", cas)

    get = (
        AsyncMock(side_effect=list(get_side_effects))
        if get_side_effects is not None
        else AsyncMock(return_value=None)
    )
    monkeypatch.setattr(engine.req_state, "get", get)

    if stage_raises is not None:
        monkeypatch.setattr(
            engine.stage_runs,
            "close_latest_stage_run",
            AsyncMock(side_effect=stage_raises),
        )
        monkeypatch.setattr(
            engine.stage_runs,
            "insert_stage_run",
            AsyncMock(side_effect=stage_raises),
        )
    else:
        monkeypatch.setattr(engine.stage_runs, "close_latest_stage_run", AsyncMock())
        monkeypatch.setattr(engine.stage_runs, "insert_stage_run", AsyncMock())

    monkeypatch.setattr(engine.obs, "record_event", AsyncMock())
    return cas, get


@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot and restore REGISTRY around each test to prevent pollution."""
    from orchestrator.actions import REGISTRY

    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


async def _step(**overrides):
    """Helper: call engine.step with sensible defaults, overrideable per test."""
    from orchestrator.engine import step
    from orchestrator.state import Event, ReqState

    defaults: dict = dict(
        pool=_POOL,
        body=_Body(),
        req_id=_REQ_ID,
        project_id=_PROJECT,
        tags=_TAGS,
        cur_state=ReqState.SPEC_LINT_RUNNING,
        ctx={},
        event=Event.SPEC_LINT_PASS,
        depth=0,
    )
    defaults.update(overrides)
    return await step(**defaults)


# ─── EAT-S1: unknown emit string is logged and dropped ───────────────────────


async def test_eat_s1_unknown_emit_string_dropped(monkeypatch) -> None:
    """
    EAT-S1: handler returning {"emit": "totally-not-an-event"} MUST NOT raise;
    engine MUST log engine.invalid_emit and return base_result without chained key.
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import ReqState

    _patch_io(monkeypatch)

    async def _handler(**_kw):
        return {"emit": "totally-not-an-event"}

    REGISTRY["start_challenger"] = _handler

    result = await _step()

    assert result.get("action") == "start_challenger", (
        f"EAT-S1: action MUST be 'start_challenger'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.CHALLENGER_RUNNING.value, (
        f"EAT-S1: next_state MUST be 'challenger-running'; got {result!r}"
    )
    assert "chained" not in result, (
        f"EAT-S1: bogus emit MUST be dropped — no 'chained' key; got {result!r}"
    )


# ─── EAT-S2: handler returning None treated as empty dict ────────────────────


async def test_eat_s2_none_result_normalised_to_empty_dict(monkeypatch) -> None:
    """
    EAT-S2: handler returns None → no raise; result["result"] MUST equal {}.
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import ReqState

    _patch_io(monkeypatch)

    async def _handler(**_kw):
        return None

    REGISTRY["start_challenger"] = _handler

    result = await _step()

    assert result.get("action") == "start_challenger", (
        f"EAT-S2: action MUST be 'start_challenger'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.CHALLENGER_RUNNING.value, (
        f"EAT-S2: next_state MUST be 'challenger-running'; got {result!r}"
    )
    assert result.get("result") == {}, (
        f"EAT-S2: None return MUST be normalised to empty dict; "
        f"got result['result']={result.get('result')!r}"
    )


# ─── EAT-S3: handler returning list treated as empty dict ────────────────────


async def test_eat_s3_list_result_treated_as_empty_dict(monkeypatch) -> None:
    """
    EAT-S3: handler returns [1,2,3] → no raise; no chained key (treated as empty dict).
    """
    from orchestrator.actions import REGISTRY

    _patch_io(monkeypatch)

    async def _handler(**_kw):
        return [1, 2, 3]

    REGISTRY["start_challenger"] = _handler

    result = await _step()

    assert result.get("action") == "start_challenger", (
        f"EAT-S3: action MUST be 'start_challenger'; got {result!r}"
    )
    assert "chained" not in result, (
        f"EAT-S3: list return MUST be treated as empty dict → no chained; got {result!r}"
    )


# ─── EAT-S4: row vanishes between dispatch and chained reload ────────────────


async def test_eat_s4_row_vanishes_mid_chain(monkeypatch) -> None:
    """
    EAT-S4: handler emits valid event but req_state.get returns None (row deleted)
    → engine MUST early-return base_result without chained key, no AttributeError.
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch, get_side_effects=[None])

    async def _handler(**_kw):
        return {"emit": Event.SPEC_LINT_PASS.value}

    REGISTRY["create_spec_lint"] = _handler

    result = await _step(
        cur_state=ReqState.ANALYZE_ARTIFACT_CHECKING,
        event=Event.ANALYZE_ARTIFACT_CHECK_PASS,
    )

    assert result.get("action") == "create_spec_lint", (
        f"EAT-S4: action MUST be 'create_spec_lint'; got {result!r}"
    )
    assert "chained" not in result, (
        f"EAT-S4: row vanished → chain MUST truncate, no 'chained' key; got {result!r}"
    )


# ─── EAT-S5: chained emit hits illegal transition ────────────────────────────


async def test_eat_s5_chained_emit_illegal_transition(monkeypatch) -> None:
    """
    EAT-S5: handler returns {"emit": "archive.done"} → after CAS to CHALLENGER_RUNNING,
    chained step hits no-transition and returns skip attached at base_result["chained"].
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch, get_side_effects=[_FakeRow(ReqState.CHALLENGER_RUNNING)])

    async def _handler(**_kw):
        return {"emit": Event.ARCHIVE_DONE.value}

    REGISTRY["start_challenger"] = _handler

    result = await _step()

    assert result.get("action") == "start_challenger", (
        f"EAT-S5: parent action MUST be 'start_challenger'; got {result!r}"
    )
    chained = result.get("chained")
    assert chained is not None, (
        f"EAT-S5: 'chained' key MUST be present when chained step ran; got {result!r}"
    )
    assert chained.get("action") == "skip", (
        f"EAT-S5: chained.action MUST be 'skip'; got chained={chained!r}"
    )
    reason = chained.get("reason") or ""
    assert "no transition challenger-running+archive.done" in reason, (
        f"EAT-S5: reason MUST contain 'no transition challenger-running+archive.done'; "
        f"got {reason!r}"
    )


# ─── EAT-S6: transition action missing from REGISTRY ─────────────────────────


async def test_eat_s6_action_not_in_registry_returns_error(monkeypatch) -> None:
    """
    EAT-S6: REGISTRY missing start_challenger → returns error dict;
    CAS MUST have been committed to CHALLENGER_RUNNING before the registry check.
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import ReqState

    cas, _ = _patch_io(monkeypatch)
    REGISTRY.pop("start_challenger", None)

    result = await _step()

    assert result.get("action") == "error", (
        f"EAT-S6: MUST return action='error'; got {result!r}"
    )
    assert result.get("reason") == "action start_challenger not registered", (
        f"EAT-S6: reason MUST equal 'action start_challenger not registered'; "
        f"got {result.get('reason')!r}"
    )
    assert cas.called, (
        "EAT-S6: cas_transition MUST have been called (CAS committed before registry check)"
    )
    # 4th positional arg (index 3) is next_state
    next_state_arg = cas.call_args.args[3]
    assert next_state_arg == ReqState.CHALLENGER_RUNNING, (
        f"EAT-S6: CAS MUST advance to CHALLENGER_RUNNING; got {next_state_arg!r}"
    )


# ─── EAT-S7: ESCALATED self-loop does not re-trigger cleanup ─────────────────


async def test_eat_s7_escalated_self_loop_no_cleanup(monkeypatch) -> None:
    """
    EAT-S7: (ESCALATED, VERIFY_ESCALATE) → action=None → no-op;
    cleanup_runner MUST NOT be triggered (cur_state already terminal).
    """
    from orchestrator import engine
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    cleanup_calls: list[tuple] = []

    class _FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            cleanup_calls.append((req_id, retain_pvc))

    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: _FakeController())

    result = await _step(
        cur_state=ReqState.ESCALATED,
        event=Event.VERIFY_ESCALATE,
    )

    assert result.get("action") == "no-op", (
        f"EAT-S7: (ESCALATED, VERIFY_ESCALATE) MUST return action='no-op'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ESCALATED.value, (
        f"EAT-S7: next_state MUST remain 'escalated'; got {result!r}"
    )
    # Yield to let any fire-and-forget tasks settle
    await asyncio.sleep(0)
    assert cleanup_calls == [], (
        f"EAT-S7: cleanup_runner MUST NOT be called on terminal self-loop; "
        f"got {len(cleanup_calls)} call(s): {cleanup_calls}"
    )


# ─── EAT-S8: stage_runs raises, engine still advances ────────────────────────


async def test_eat_s8_stage_runs_failure_does_not_propagate(monkeypatch) -> None:
    """
    EAT-S8: stage_runs INSERT raises RuntimeError → engine.step MUST NOT raise;
    transition MUST still be committed (best-effort observability).
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import ReqState

    _patch_io(monkeypatch, stage_raises=RuntimeError("DB down"))

    async def _handler(**_kw):
        return {}

    REGISTRY["start_challenger"] = _handler

    result = await _step()

    assert result.get("action") == "start_challenger", (
        f"EAT-S8: MUST return action='start_challenger' despite stage_runs failure; got {result!r}"
    )
    assert result.get("next_state") == ReqState.CHALLENGER_RUNNING.value, (
        f"EAT-S8: transition MUST be committed; next_state MUST be 'challenger-running'; got {result!r}"
    )


# ─── EAT-S9: body without issueId does not raise ─────────────────────────────


async def test_eat_s9_body_without_issue_id_no_raise(monkeypatch) -> None:
    """
    EAT-S9: body with no issueId attribute → engine.step MUST NOT raise AttributeError;
    result MUST contain action='start_challenger'.
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import ReqState

    _patch_io(monkeypatch)

    async def _handler(**_kw):
        return {}

    REGISTRY["start_challenger"] = _handler

    result = await _step(body=_NoAttrBody())

    assert result.get("action") == "start_challenger", (
        f"EAT-S9: MUST return action='start_challenger'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.CHALLENGER_RUNNING.value, (
        f"EAT-S9: MUST return next_state='challenger-running'; got {result!r}"
    )


# ─── EAT-S10a: depth=12 still dispatches handler ────────────────────────────


async def test_eat_s10a_depth_12_dispatches_handler(monkeypatch) -> None:
    """
    EAT-S10a: depth=12 is the last valid depth; handler MUST be called exactly once.
    """
    from orchestrator.actions import REGISTRY

    _patch_io(monkeypatch)
    calls: list[int] = []

    async def _handler(**_kw):
        calls.append(1)
        return {}

    REGISTRY["start_challenger"] = _handler

    result = await _step(depth=12)

    assert len(calls) == 1, (
        f"EAT-S10a: handler MUST be called exactly once at depth=12; "
        f"got {len(calls)} call(s)"
    )
    assert result.get("action") == "start_challenger", (
        f"EAT-S10a: MUST return action='start_challenger'; got {result!r}"
    )


# ─── EAT-S10b: depth=13 triggers recursion guard ────────────────────────────


async def test_eat_s10b_depth_13_recursion_guard_fires() -> None:
    """
    EAT-S10b: depth=13 → engine MUST return recursion error dict without calling handler.
    """
    from orchestrator.actions import REGISTRY

    calls: list[int] = []

    async def _handler(**_kw):
        calls.append(1)
        return {}

    REGISTRY["start_challenger"] = _handler

    result = await _step(depth=13)

    assert calls == [], (
        f"EAT-S10b: handler MUST NOT be called at depth=13; got {len(calls)} call(s)"
    )
    assert result == {"action": "error", "reason": "engine recursion >12"}, (
        f"EAT-S10b: MUST return exact recursion error dict; got {result!r}"
    )


# ─── EAT-S11: SESSION_FAILED on DONE is skipped ──────────────────────────────


async def test_eat_s11_session_failed_on_done_skipped() -> None:
    """
    EAT-S11: (DONE, SESSION_FAILED) → action='skip'; escalate MUST NOT be invoked.
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    escalate_calls: list[int] = []

    async def _fake_escalate(**_kw):
        escalate_calls.append(1)
        return {}

    REGISTRY["escalate"] = _fake_escalate

    result = await _step(
        cur_state=ReqState.DONE,
        event=Event.SESSION_FAILED,
    )

    assert result.get("action") == "skip", (
        f"EAT-S11: MUST return action='skip'; got {result!r}"
    )
    reason = (result.get("reason") or "").lower()
    assert reason.startswith("no transition done+"), (
        f"EAT-S11: reason MUST start with 'no transition done+'; got {result.get('reason')!r}"
    )
    assert escalate_calls == [], (
        f"EAT-S11: escalate MUST NOT be invoked on terminal DONE; "
        f"got {len(escalate_calls)} call(s)"
    )


# ─── EAT-S12: DONE terminal skips every Event ────────────────────────────────


async def test_eat_s12_done_skips_every_event() -> None:
    """
    EAT-S12: engine.step(DONE, event) MUST return action='skip' for every Event member;
    no handler MUST be invoked; no call MUST raise.
    """
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    handler_calls: list[str] = []

    async def _any_handler(**_kw):
        handler_calls.append("called")
        return {}

    for name in list(REGISTRY.keys()):
        REGISTRY[name] = _any_handler
    for name in [
        "start_intake", "start_analyze", "start_challenger", "escalate",
        "create_spec_lint", "create_staging_test", "create_pr_ci_watch",
        "create_accept", "done_archive",
        "invoke_verifier_for_analyze_artifact_check_fail",
    ]:
        REGISTRY[name] = _any_handler

    failures: list[str] = []
    for event in Event:
        try:
            result = await _step(cur_state=ReqState.DONE, event=event)
        except Exception as exc:
            failures.append(f"{event.value}: raised {exc!r}")
            continue
        if result.get("action") != "skip":
            failures.append(
                f"{event.value}: action={result.get('action')!r} (want 'skip')"
            )

    if failures:
        pytest.fail(
            "EAT-S12: DONE MUST skip every Event:\n" + "\n".join(failures)
        )

    assert handler_calls == [], (
        f"EAT-S12: no handler MUST be invoked for DONE terminal state; "
        f"got {len(handler_calls)} call(s)"
    )
