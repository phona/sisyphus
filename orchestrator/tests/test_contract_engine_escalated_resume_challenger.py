"""Challenger contract tests for REQ-test-coverage-escalated-resume-1777281969.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-test-coverage-escalated-resume-1777281969/specs/engine-escalated-resume-tests/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  ERT-S1  (INIT, INTENT_INTAKE) → INTAKING + start_intake dispatched exactly once
  ERT-S2  (INTAKING, INTAKE_PASS) → ANALYZING + start_analyze_with_finalized_intent dispatched
  ERT-S3  (INTAKING, INTAKE_FAIL) → ESCALATED + escalate + cleanup_runner(retain_pvc=True)
  ERT-S4  (INTAKING, VERIFY_ESCALATE) → ESCALATED + escalate + cleanup_runner(retain_pvc=True)
  ERT-S5  (ANALYZING, VERIFY_ESCALATE) → ESCALATED + escalate + cleanup_runner(retain_pvc=True)
  ERT-S6  (PR_CI_RUNNING, PR_CI_TIMEOUT) → ESCALATED + escalate + cleanup_runner(retain_pvc=True)
  ERT-S7  ESCALATED + VERIFY_PASS → apply_verify_pass chains to create_pr_ci_watch, final PR_CI_RUNNING
  ERT-S8  ESCALATED + VERIFY_FIX_NEEDED → FIXER_RUNNING, start_fixer receives verify:staging_test tag + ctx
  ERT-S9  47/47 TRANSITIONS sweep — every declared transition round-trips through engine.step

Module contract under test: orchestrator.engine.step
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

# ─── Shared helpers ───────────────────────────────────────────────────────────

_POOL = object()  # sentinel; all DB calls are patched away
_REQ_ID = "REQ-test-coverage-escalated-resume-1777281969"
_PROJECT = "proj-ert"


class _Body:
    issueId = "bkd-ert-test"
    projectId = _PROJECT


class _FakeRow:
    def __init__(self, state, context=None):
        self.state = state
        self.context = context or {}


def _patch_io(
    monkeypatch,
    *,
    get_side_effects=None,
    cas_return: bool = True,
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

    monkeypatch.setattr(engine.stage_runs, "close_latest_stage_run", AsyncMock())
    monkeypatch.setattr(engine.stage_runs, "insert_stage_run", AsyncMock())
    monkeypatch.setattr(engine.obs, "record_event", AsyncMock())

    return cas, get


@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot and restore REGISTRY + ACTION_META around each test."""
    from orchestrator.actions import ACTION_META, REGISTRY

    snap_reg = dict(REGISTRY)
    snap_meta = dict(ACTION_META)
    yield
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(snap_reg)
    ACTION_META.update(snap_meta)


async def _step(**overrides):
    from orchestrator.engine import step
    from orchestrator.state import Event, ReqState

    defaults: dict = dict(
        pool=_POOL,
        body=_Body(),
        req_id=_REQ_ID,
        project_id=_PROJECT,
        tags=[_REQ_ID],
        cur_state=ReqState.SPEC_LINT_RUNNING,
        ctx={},
        event=Event.SPEC_LINT_FAIL,
        depth=0,
    )
    defaults.update(overrides)
    return await step(**defaults)


# ─── ERT-S1: (INIT, INTENT_INTAKE) → INTAKING + start_intake ─────────────────


async def test_ert_s1_init_intent_intake_enters_intaking(monkeypatch) -> None:
    """ERT-S1: (INIT, INTENT_INTAKE) → INTAKING via start_intake dispatched exactly once.
    Intake is the human-in-loop clarification gate; a missing transition silently strands
    the REQ before any work begins."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas, _ = _patch_io(monkeypatch)
    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("start_intake")
        return {}

    REGISTRY["start_intake"] = _stub

    result = await _step(cur_state=ReqState.INIT, event=Event.INTENT_INTAKE)

    assert result.get("action") == "start_intake", (
        f"ERT-S1: action MUST be 'start_intake'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.INTAKING.value, (
        f"ERT-S1: next_state MUST be {ReqState.INTAKING.value!r}; got {result!r}"
    )
    assert len(calls) == 1, (
        f"ERT-S1: start_intake MUST be awaited exactly once; called {len(calls)} time(s)"
    )
    assert cas.called, "ERT-S1: cas_transition MUST have been called"
    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.INTAKING, (
        f"ERT-S1: CAS MUST advance to INTAKING; got {cas_next!r}"
    )


# ─── ERT-S2: (INTAKING, INTAKE_PASS) → ANALYZING + start_analyze_with_finalized_intent ──


async def test_ert_s2_intaking_intake_pass_enters_analyzing(monkeypatch) -> None:
    """ERT-S2: (INTAKING, INTAKE_PASS) → ANALYZING via start_analyze_with_finalized_intent.
    Intake clarification succeeded; finalized intent is forwarded to the analyze phase."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas, _ = _patch_io(monkeypatch)
    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("start_analyze_with_finalized_intent")
        return {}

    REGISTRY["start_analyze_with_finalized_intent"] = _stub

    result = await _step(cur_state=ReqState.INTAKING, event=Event.INTAKE_PASS)

    assert result.get("action") == "start_analyze_with_finalized_intent", (
        f"ERT-S2: action MUST be 'start_analyze_with_finalized_intent'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ANALYZING.value, (
        f"ERT-S2: next_state MUST be {ReqState.ANALYZING.value!r}; got {result!r}"
    )
    assert len(calls) == 1, (
        f"ERT-S2: stub MUST be awaited exactly once; called {len(calls)} time(s)"
    )
    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.ANALYZING, (
        f"ERT-S2: CAS MUST advance to ANALYZING; got {cas_next!r}"
    )


# ─── ERT-S3..S6: four escalate paths + cleanup_runner(retain_pvc=True) ───────


_ESCALATE_CASES = [
    pytest.param(
        "INTAKING", "INTAKE_FAIL",
        id="ERT-S3-intaking_intake_fail",
    ),
    pytest.param(
        "INTAKING", "VERIFY_ESCALATE",
        id="ERT-S4-intaking_verify_escalate",
    ),
    pytest.param(
        "ANALYZING", "VERIFY_ESCALATE",
        id="ERT-S5-analyzing_verify_escalate",
    ),
    pytest.param(
        "PR_CI_RUNNING", "PR_CI_TIMEOUT",
        id="ERT-S6-pr_ci_timeout",
    ),
]


@pytest.mark.parametrize("state_name,event_name", _ESCALATE_CASES)
async def test_ert_s3_to_s6_non_terminal_escalate_triggers_cleanup(
    monkeypatch, state_name, event_name,
) -> None:
    """ERT-S3..S6: (INTAKING|ANALYZING|PR_CI_RUNNING, *) → ESCALATED via escalate.
    Non-terminal → terminal transition MUST fire-and-forget cleanup_runner(retain_pvc=True)
    because the runner PVC must be preserved for post-mortem inspection."""
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas, _ = _patch_io(monkeypatch)
    cleanup_calls: list[tuple] = []

    class _FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            cleanup_calls.append((req_id, retain_pvc))

    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: _FakeController())

    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("escalate")
        return {}

    REGISTRY["escalate"] = _stub

    cur = ReqState[state_name]
    evt = Event[event_name]

    result = await _step(cur_state=cur, event=evt, tags=[_REQ_ID])
    await asyncio.sleep(0)

    tag = f"[{state_name}+{event_name}]"

    assert result.get("action") == "escalate", (
        f"{tag}: action MUST be 'escalate'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ESCALATED.value, (
        f"{tag}: next_state MUST be {ReqState.ESCALATED.value!r}; got {result!r}"
    )
    assert len(calls) == 1, (
        f"{tag}: escalate MUST be awaited exactly once; called {len(calls)} time(s)"
    )
    assert cas.called, f"{tag}: cas_transition MUST have been called"
    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.ESCALATED, (
        f"{tag}: CAS MUST advance to ESCALATED; got {cas_next!r}"
    )
    assert len(cleanup_calls) == 1, (
        f"{tag}: cleanup_runner MUST be called exactly once; got {cleanup_calls!r}"
    )
    assert cleanup_calls[0] == (_REQ_ID, True), (
        f"{tag}: cleanup_runner MUST be called with (req_id, retain_pvc=True); "
        f"got {cleanup_calls[0]!r}"
    )


# ─── ERT-S7: ESCALATED + VERIFY_PASS chains end-to-end to PR_CI_RUNNING ──────


async def test_ert_s7_escalated_verify_pass_chains_to_pr_ci_running(monkeypatch) -> None:
    """ERT-S7: ESCALATED + VERIFY_PASS → apply_verify_pass chains to create_pr_ci_watch.
    apply_verify_pass internally CAS-advances to STAGING_TEST_RUNNING and emits
    staging-test.pass; engine MUST chain-dispatch create_pr_ci_watch; final state = PR_CI_RUNNING.
    This is the sole human-driven path to resume a stranded REQ end-to-end."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(
        monkeypatch,
        get_side_effects=[_FakeRow(ReqState.STAGING_TEST_RUNNING)],
    )

    avp_calls: list[int] = []
    cpcw_calls: list[int] = []

    async def _apply_verify_pass(**_kw):
        avp_calls.append(1)
        return {"emit": "staging-test.pass"}

    async def _create_pr_ci_watch(**_kw):
        cpcw_calls.append(1)
        return {}

    REGISTRY["apply_verify_pass"] = _apply_verify_pass
    REGISTRY["create_pr_ci_watch"] = _create_pr_ci_watch

    result = await _step(
        cur_state=ReqState.ESCALATED,
        event=Event.VERIFY_PASS,
        tags=["verifier", _REQ_ID, "verify:staging_test"],
        ctx={"verifier_stage": "staging_test"},
    )

    assert result.get("action") == "apply_verify_pass", (
        f"ERT-S7: action MUST be 'apply_verify_pass'; got {result!r}"
    )
    assert len(avp_calls) == 1, (
        f"ERT-S7: apply_verify_pass MUST be awaited exactly once; got {len(avp_calls)}"
    )
    assert len(cpcw_calls) == 1, (
        f"ERT-S7: create_pr_ci_watch MUST be awaited exactly once (chained); got {len(cpcw_calls)}"
    )

    chained = result.get("chained")
    assert chained is not None, (
        f"ERT-S7: result MUST contain 'chained' sub-result; got {result!r}"
    )
    assert chained.get("action") == "create_pr_ci_watch", (
        f"ERT-S7: chained.action MUST be 'create_pr_ci_watch'; got chained={chained!r}"
    )
    assert chained.get("next_state") == ReqState.PR_CI_RUNNING.value, (
        f"ERT-S7: chained.next_state MUST be {ReqState.PR_CI_RUNNING.value!r}; "
        f"got chained={chained!r}"
    )


# ─── ERT-S8: ESCALATED + VERIFY_FIX_NEEDED → FIXER_RUNNING + tag forwarding ──


async def test_ert_s8_escalated_verify_fix_needed_forwards_stage_tag_to_fixer(
    monkeypatch,
) -> None:
    """ERT-S8: ESCALATED + VERIFY_FIX_NEEDED → FIXER_RUNNING via start_fixer.
    Engine MUST forward verify:staging_test tag and ctx verifier_stage=staging_test
    to start_fixer, because start_fixer reads stage from the issue tag to resolve
    multi-verifier-concurrent ctx race."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas, _ = _patch_io(monkeypatch)
    recorded: dict = {}

    async def _stub(**kw):
        recorded["tags"] = list(kw.get("tags", []))
        recorded["ctx"] = dict(kw.get("ctx", {}))
        return {}

    REGISTRY["start_fixer"] = _stub

    result = await _step(
        cur_state=ReqState.ESCALATED,
        event=Event.VERIFY_FIX_NEEDED,
        tags=["verifier", _REQ_ID, "verify:staging_test"],
        ctx={"verifier_stage": "staging_test", "verifier_fixer": "dev"},
    )

    assert result.get("action") == "start_fixer", (
        f"ERT-S8: action MUST be 'start_fixer'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.FIXER_RUNNING.value, (
        f"ERT-S8: next_state MUST be {ReqState.FIXER_RUNNING.value!r}; got {result!r}"
    )
    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.FIXER_RUNNING, (
        f"ERT-S8: CAS MUST advance to FIXER_RUNNING; got {cas_next!r}"
    )

    tags = recorded.get("tags", [])
    assert "verify:staging_test" in tags, (
        f"ERT-S8: start_fixer MUST receive 'verify:staging_test' tag; got tags={tags!r}"
    )
    ctx = recorded.get("ctx", {})
    assert ctx.get("verifier_stage") == "staging_test", (
        f"ERT-S8: start_fixer MUST receive ctx.verifier_stage='staging_test'; got ctx={ctx!r}"
    )


# ─── ERT-S9: 47/47 TRANSITIONS sweep ─────────────────────────────────────────


async def test_ert_s9_all_transitions_round_trip_engine_step(monkeypatch) -> None:
    """ERT-S9: every entry in state.TRANSITIONS round-trips through engine.step.
    Defense-in-depth sweep: if any granular per-transition test (ERT-S1..S8, VLT, MCT, APT)
    drifts away from the transition table, this test catches the regression."""
    from orchestrator import engine
    from orchestrator.actions import ACTION_META, REGISTRY
    from orchestrator.state import TRANSITIONS

    _patch_io(monkeypatch)

    class _FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            pass

    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: _FakeController())

    failures: list[str] = []

    for (state, event), transition in TRANSITIONS.items():
        snap_reg = dict(REGISTRY)
        snap_meta = dict(ACTION_META)
        try:
            action_name = transition.action

            if action_name is not None:
                async def _stub(_a=action_name, **_kw):
                    return {}
                REGISTRY[action_name] = _stub

            result = await _step(
                cur_state=state,
                event=event,
                tags=[_REQ_ID],
                ctx={},
            )
            await asyncio.sleep(0)

            expected_action = action_name if action_name is not None else "no-op"
            got_action = result.get("action")
            got_next = result.get("next_state")
            expected_next = transition.next_state.value

            if got_action != expected_action:
                failures.append(
                    f"({state.value}, {event.value}): "
                    f"action MUST be {expected_action!r}; got {got_action!r}"
                )
            if got_next != expected_next:
                failures.append(
                    f"({state.value}, {event.value}): "
                    f"next_state MUST be {expected_next!r}; got {got_next!r}"
                )

        except Exception as exc:
            failures.append(
                f"({state.value}, {event.value}): raised {type(exc).__name__}: {exc}"
            )
        finally:
            REGISTRY.clear()
            ACTION_META.clear()
            REGISTRY.update(snap_reg)
            ACTION_META.update(snap_meta)

    if failures:
        pytest.fail(
            f"ERT-S9: {len(failures)} transition(s) failed out of {len(TRANSITIONS)}:\n"
            + "\n".join(failures)
        )
