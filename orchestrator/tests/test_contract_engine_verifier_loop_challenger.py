"""Challenger contract tests for REQ-test-verifier-loop-1777267725.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-test-verifier-loop-1777267725/specs/engine-verifier-loop-tests/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  VLT-S1   (SPEC_LINT_RUNNING, SPEC_LINT_FAIL) → REVIEW_RUNNING + invoke_verifier_for_spec_lint_fail
  VLT-S2   (DEV_CROSS_CHECK_RUNNING, DEV_CROSS_CHECK_FAIL) → REVIEW_RUNNING + invoke_verifier_for_dev_cross_check_fail
  VLT-S3   (STAGING_TEST_RUNNING, STAGING_TEST_FAIL) → REVIEW_RUNNING + invoke_verifier_for_staging_test_fail
  VLT-S4   (PR_CI_RUNNING, PR_CI_FAIL) → REVIEW_RUNNING + invoke_verifier_for_pr_ci_fail
  VLT-S5   (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) → REVIEW_RUNNING + invoke_verifier_for_accept_fail
  VLT-S6   (ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_FAIL) → REVIEW_RUNNING + invoke_verifier_for_analyze_artifact_check_fail
  VLT-S7   (CHALLENGER_RUNNING, CHALLENGER_FAIL) → REVIEW_RUNNING + invoke_verifier_for_challenger_fail
  VLT-S8   (REVIEW_RUNNING, VERIFY_FIX_NEEDED) → FIXER_RUNNING, stage_runs: close verifier/fix + insert fixer
  VLT-S9   (REVIEW_RUNNING, VERIFY_ESCALATE) → ESCALATED + cleanup_runner fire-and-forget
  VLT-S10  (FIXER_RUNNING, FIXER_DONE) → REVIEW_RUNNING, stage_runs: close fixer/pass + insert verifier
  VLT-S11  (FIXER_RUNNING, VERIFY_ESCALATE) → ESCALATED + escalate dispatched
  VLT-S12  (ESCALATED, VERIFY_PASS) → ESCALATED self-loop + apply_verify_pass dispatched, no cleanup
  VLT-S13  (ESCALATED, VERIFY_FIX_NEEDED) → FIXER_RUNNING + start_fixer dispatched
  VLT-S14  (ESCALATED, VERIFY_ESCALATE) → ESCALATED no-op self-loop, no cleanup_runner
  VLT-S15  13× (*_RUNNING, SESSION_FAILED) → self-loop + escalate dispatch, no cleanup
  VLT-S16  (INIT, SESSION_FAILED) → skip, reason contains "no transition init+session.failed"

Module contract under test: orchestrator.engine.step
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─── Shared helpers ───────────────────────────────────────────────────────────

_POOL = object()  # sentinel; DB layer is fully patched
_REQ_ID = "REQ-test-verifier-loop-1777267725"
_PROJECT = "proj-vlt"


class _Body:
    issueId = "bkd-vlt-test"
    projectId = _PROJECT


def _patch_io(
    monkeypatch,
    *,
    cas_return: bool = True,
    stage_raises: Exception | None = None,
) -> AsyncMock:
    """Patch all engine external I/O. Returns cas_mock."""
    from orchestrator import engine

    cas = AsyncMock(return_value=cas_return)
    monkeypatch.setattr(engine.req_state, "cas_transition", cas)
    monkeypatch.setattr(engine.req_state, "get", AsyncMock(return_value=None))

    if stage_raises is not None:
        monkeypatch.setattr(
            engine.stage_runs, "close_latest_stage_run", AsyncMock(side_effect=stage_raises),
        )
        monkeypatch.setattr(
            engine.stage_runs, "insert_stage_run", AsyncMock(side_effect=stage_raises),
        )
    else:
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


# ─── VLT-S1..S7: upstream *_FAIL → REVIEW_RUNNING + invoke_verifier_for_<stage>_fail ──


_ENTRY_CASES = [
    pytest.param(
        "SPEC_LINT_RUNNING", "SPEC_LINT_FAIL",
        "invoke_verifier_for_spec_lint_fail",
        id="VLT-S1-spec_lint_fail",
    ),
    pytest.param(
        "DEV_CROSS_CHECK_RUNNING", "DEV_CROSS_CHECK_FAIL",
        "invoke_verifier_for_dev_cross_check_fail",
        id="VLT-S2-dev_cross_check_fail",
    ),
    pytest.param(
        "STAGING_TEST_RUNNING", "STAGING_TEST_FAIL",
        "invoke_verifier_for_staging_test_fail",
        id="VLT-S3-staging_test_fail",
    ),
    pytest.param(
        "PR_CI_RUNNING", "PR_CI_FAIL",
        "invoke_verifier_for_pr_ci_fail",
        id="VLT-S4-pr_ci_fail",
    ),
    pytest.param(
        "ACCEPT_TEARING_DOWN", "TEARDOWN_DONE_FAIL",
        "invoke_verifier_for_accept_fail",
        id="VLT-S5-accept_teardown_fail",
    ),
    pytest.param(
        "ANALYZE_ARTIFACT_CHECKING", "ANALYZE_ARTIFACT_CHECK_FAIL",
        "invoke_verifier_for_analyze_artifact_check_fail",
        id="VLT-S6-analyze_artifact_check_fail",
    ),
    pytest.param(
        "CHALLENGER_RUNNING", "CHALLENGER_FAIL",
        "invoke_verifier_for_challenger_fail",
        id="VLT-S7-challenger_fail",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name,event_name,expected_action", _ENTRY_CASES)
async def test_vlt_s1_to_s7_upstream_fail_enters_review_running(
    monkeypatch, state_name, event_name, expected_action,
) -> None:
    """VLT-S1..S7: every upstream *_FAIL dispatches invoke_verifier_for_<stage>_fail
    and CAS-advances to REVIEW_RUNNING."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_io(monkeypatch)
    calls: list[str] = []

    async def _stub(**_kw):
        calls.append(expected_action)
        return {}

    REGISTRY[expected_action] = _stub

    cur = ReqState[state_name]
    evt = Event[event_name]

    result = await _step(cur_state=cur, event=evt, tags=[_REQ_ID])

    assert result.get("action") == expected_action, (
        f"{state_name}: action MUST be '{expected_action}'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.REVIEW_RUNNING.value, (
        f"{state_name}: next_state MUST be 'review-running'; got {result!r}"
    )
    assert len(calls) == 1, (
        f"{state_name}: stub MUST be awaited exactly once; called {len(calls)} time(s)"
    )
    # CAS must commit to REVIEW_RUNNING
    assert cas.called, f"{state_name}: cas_transition MUST have been called"
    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.REVIEW_RUNNING, (
        f"{state_name}: CAS MUST advance to REVIEW_RUNNING; got {cas_next!r}"
    )


# ─── VLT-S8: REVIEW_RUNNING + VERIFY_FIX_NEEDED → FIXER_RUNNING + stage_runs roll ──


@pytest.mark.asyncio
async def test_vlt_s8_verify_fix_needed_enters_fixer_running_with_stage_runs(
    monkeypatch,
) -> None:
    """VLT-S8: (REVIEW_RUNNING, VERIFY_FIX_NEEDED) → FIXER_RUNNING via start_fixer.
    Engine MUST close verifier stage_run (outcome=fix) and insert a fixer stage_run."""
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_io(monkeypatch)
    close_mock = engine.stage_runs.close_latest_stage_run
    insert_mock = engine.stage_runs.insert_stage_run

    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("start_fixer")
        return {}

    REGISTRY["start_fixer"] = _stub

    result = await _step(
        cur_state=ReqState.REVIEW_RUNNING,
        event=Event.VERIFY_FIX_NEEDED,
        tags=["verifier", _REQ_ID, "verify:dev_cross_check"],
        ctx={"verifier_stage": "dev_cross_check"},
    )

    assert result.get("action") == "start_fixer", (
        f"VLT-S8: action MUST be 'start_fixer'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.FIXER_RUNNING.value, (
        f"VLT-S8: next_state MUST be 'fixer-running'; got {result!r}"
    )
    assert len(calls) == 1, f"VLT-S8: start_fixer MUST be awaited exactly once"

    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.FIXER_RUNNING, (
        f"VLT-S8: CAS MUST advance to FIXER_RUNNING; got {cas_next!r}"
    )

    close_calls = close_mock.call_args_list
    insert_calls = insert_mock.call_args_list
    assert len(close_calls) == 1, (
        f"VLT-S8: exactly 1 stage_run close MUST be recorded; got {close_calls!r}"
    )
    assert len(insert_calls) == 1, (
        f"VLT-S8: exactly 1 stage_run insert MUST be recorded; got {insert_calls!r}"
    )
    # signature: close_latest_stage_run(pool, req_id, stage, *, outcome=...)
    close_call = close_calls[0]
    assert close_call.args[2] == "verifier", (
        f"VLT-S8: close MUST target stage='verifier'; got args={close_call.args!r}"
    )
    assert close_call.kwargs.get("outcome") == "fix", (
        f"VLT-S8: close MUST record outcome='fix'; got kwargs={close_call.kwargs!r}"
    )
    # signature: insert_stage_run(pool, req_id, stage, ...)
    insert_call = insert_calls[0]
    assert insert_call.args[2] == "fixer", (
        f"VLT-S8: insert MUST target stage='fixer'; got args={insert_call.args!r}"
    )


# ─── VLT-S9: REVIEW_RUNNING + VERIFY_ESCALATE → ESCALATED + cleanup_runner ──


@pytest.mark.asyncio
async def test_vlt_s9_verify_escalate_enters_escalated_and_triggers_cleanup(
    monkeypatch,
) -> None:
    """VLT-S9: (REVIEW_RUNNING, VERIFY_ESCALATE) → ESCALATED via escalate.
    Non-terminal → terminal: engine MUST fire-and-forget cleanup_runner(retain_pvc=True)."""
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

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

    result = await _step(
        cur_state=ReqState.REVIEW_RUNNING,
        event=Event.VERIFY_ESCALATE,
        tags=["verifier", _REQ_ID],
        ctx={},
    )
    await asyncio.sleep(0)

    assert result.get("action") == "escalate", (
        f"VLT-S9: action MUST be 'escalate'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ESCALATED.value, (
        f"VLT-S9: next_state MUST be 'escalated'; got {result!r}"
    )
    assert len(calls) == 1, "VLT-S9: escalate MUST be awaited exactly once"
    assert len(cleanup_calls) == 1, (
        f"VLT-S9: cleanup_runner MUST be called exactly once; got {cleanup_calls!r}"
    )
    assert cleanup_calls[0] == (_REQ_ID, True), (
        f"VLT-S9: cleanup_runner MUST be called with (req_id, retain_pvc=True); "
        f"got {cleanup_calls[0]!r}"
    )


# ─── VLT-S10: FIXER_RUNNING + FIXER_DONE → REVIEW_RUNNING ───────────────────


@pytest.mark.asyncio
async def test_vlt_s10_fixer_done_returns_to_review_running(monkeypatch) -> None:
    """VLT-S10: (FIXER_RUNNING, FIXER_DONE) → REVIEW_RUNNING via invoke_verifier_after_fix.
    Engine MUST close fixer stage_run (outcome=pass) and insert a verifier stage_run."""
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_io(monkeypatch)
    close_mock = engine.stage_runs.close_latest_stage_run
    insert_mock = engine.stage_runs.insert_stage_run

    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("invoke_verifier_after_fix")
        return {}

    REGISTRY["invoke_verifier_after_fix"] = _stub

    result = await _step(
        cur_state=ReqState.FIXER_RUNNING,
        event=Event.FIXER_DONE,
        tags=["fixer", _REQ_ID, "parent-stage:dev_cross_check"],
        ctx={"fixer_role": "dev"},
    )

    assert result.get("action") == "invoke_verifier_after_fix", (
        f"VLT-S10: action MUST be 'invoke_verifier_after_fix'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.REVIEW_RUNNING.value, (
        f"VLT-S10: next_state MUST be 'review-running'; got {result!r}"
    )
    assert len(calls) == 1, "VLT-S10: invoke_verifier_after_fix MUST be awaited exactly once"

    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.REVIEW_RUNNING, (
        f"VLT-S10: CAS MUST advance to REVIEW_RUNNING; got {cas_next!r}"
    )

    close_calls = close_mock.call_args_list
    insert_calls = insert_mock.call_args_list
    assert len(close_calls) == 1, (
        f"VLT-S10: exactly 1 close MUST be recorded; got {close_calls!r}"
    )
    assert len(insert_calls) == 1, (
        f"VLT-S10: exactly 1 insert MUST be recorded; got {insert_calls!r}"
    )
    # signature: close_latest_stage_run(pool, req_id, stage, *, outcome=...)
    close_call = close_calls[0]
    assert close_call.args[2] == "fixer", (
        f"VLT-S10: close MUST target stage='fixer'; got args={close_call.args!r}"
    )
    assert close_call.kwargs.get("outcome") == "pass", (
        f"VLT-S10: close MUST record outcome='pass'; got kwargs={close_call.kwargs!r}"
    )
    # signature: insert_stage_run(pool, req_id, stage, ...)
    insert_call = insert_calls[0]
    assert insert_call.args[2] == "verifier", (
        f"VLT-S10: insert MUST target stage='verifier'; got args={insert_call.args!r}"
    )


# ─── VLT-S11: FIXER_RUNNING + VERIFY_ESCALATE → ESCALATED (round-cap escape) ─


@pytest.mark.asyncio
async def test_vlt_s11_fixer_round_cap_escapes_to_escalated(monkeypatch) -> None:
    """VLT-S11: (FIXER_RUNNING, VERIFY_ESCALATE) → ESCALATED via escalate.
    Round-cap escape path: start_fixer itself emits VERIFY_ESCALATE when cap hit."""
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

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

    result = await _step(
        cur_state=ReqState.FIXER_RUNNING,
        event=Event.VERIFY_ESCALATE,
        tags=["fixer", _REQ_ID],
        ctx={"fixer_round": 5},
    )
    await asyncio.sleep(0)

    assert result.get("action") == "escalate", (
        f"VLT-S11: action MUST be 'escalate'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ESCALATED.value, (
        f"VLT-S11: next_state MUST be 'escalated'; got {result!r}"
    )
    assert len(calls) == 1, "VLT-S11: escalate MUST be awaited exactly once"
    assert len(cleanup_calls) == 1, (
        f"VLT-S11: cleanup_runner MUST fire-and-forget once; got {cleanup_calls!r}"
    )


# ─── VLT-S12: ESCALATED + VERIFY_PASS → self-loop, apply_verify_pass ─────────


@pytest.mark.asyncio
async def test_vlt_s12_escalated_verify_pass_dispatches_apply_verify_pass(
    monkeypatch,
) -> None:
    """VLT-S12: (ESCALATED, VERIFY_PASS) → ESCALATED self-loop + apply_verify_pass.
    cur_state is terminal → engine MUST NOT trigger cleanup_runner."""
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_io(monkeypatch)

    cleanup_calls: list[tuple] = []

    class _FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            cleanup_calls.append((req_id, retain_pvc))

    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: _FakeController())

    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("apply_verify_pass")
        return {}

    REGISTRY["apply_verify_pass"] = _stub

    result = await _step(
        cur_state=ReqState.ESCALATED,
        event=Event.VERIFY_PASS,
        tags=["verifier", _REQ_ID, "verify:pr_ci"],
        ctx={"verifier_stage": "pr_ci"},
    )
    await asyncio.sleep(0)

    assert result.get("action") == "apply_verify_pass", (
        f"VLT-S12: action MUST be 'apply_verify_pass'; got {result!r}"
    )
    # self-loop: CAS next_state == ESCALATED
    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.ESCALATED, (
        f"VLT-S12: CAS MUST be self-loop to ESCALATED; got {cas_next!r}"
    )
    assert len(calls) == 1, "VLT-S12: apply_verify_pass MUST be awaited exactly once"
    assert cleanup_calls == [], (
        f"VLT-S12: cleanup_runner MUST NOT be triggered (cur is terminal); "
        f"got {cleanup_calls!r}"
    )


# ─── VLT-S13: ESCALATED + VERIFY_FIX_NEEDED → FIXER_RUNNING ─────────────────


@pytest.mark.asyncio
async def test_vlt_s13_escalated_verify_fix_needed_enters_fixer_running(
    monkeypatch,
) -> None:
    """VLT-S13: (ESCALATED, VERIFY_FIX_NEEDED) → FIXER_RUNNING via start_fixer.
    Human-resume path: user re-opens verifier from ESCALATED and decides fix."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_io(monkeypatch)
    calls: list[str] = []

    async def _stub(**_kw):
        calls.append("start_fixer")
        return {}

    REGISTRY["start_fixer"] = _stub

    result = await _step(
        cur_state=ReqState.ESCALATED,
        event=Event.VERIFY_FIX_NEEDED,
        tags=["verifier", _REQ_ID, "verify:staging_test"],
        ctx={"verifier_stage": "staging_test", "verifier_fixer": "dev"},
    )

    assert result.get("action") == "start_fixer", (
        f"VLT-S13: action MUST be 'start_fixer'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.FIXER_RUNNING.value, (
        f"VLT-S13: next_state MUST be 'fixer-running'; got {result!r}"
    )
    assert len(calls) == 1, "VLT-S13: start_fixer MUST be awaited exactly once"

    cas_next = cas.call_args.args[3]
    assert cas_next == ReqState.FIXER_RUNNING, (
        f"VLT-S13: CAS MUST advance to FIXER_RUNNING; got {cas_next!r}"
    )


# ─── VLT-S14: ESCALATED + VERIFY_ESCALATE → no-op self-loop, no cleanup ─────


@pytest.mark.asyncio
async def test_vlt_s14_escalated_verify_escalate_is_no_op_no_cleanup(monkeypatch) -> None:
    """VLT-S14: (ESCALATED, VERIFY_ESCALATE) → ESCALATED self-loop, action=no-op.
    Terminal self-loop: cleanup_runner MUST NOT be triggered (already cleaned up)."""
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
        tags=["verifier", _REQ_ID],
        ctx={},
    )
    await asyncio.sleep(0)

    assert result.get("action") == "no-op", (
        f"VLT-S14: action MUST be 'no-op'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ESCALATED.value, (
        f"VLT-S14: next_state MUST remain 'escalated'; got {result!r}"
    )
    assert cleanup_calls == [], (
        f"VLT-S14: cleanup_runner MUST NOT be called on terminal self-loop; "
        f"got {cleanup_calls!r}"
    )


# ─── VLT-S15: SESSION_FAILED self-loops on every *_RUNNING state ─────────────


_SESSION_FAILED_STATE_NAMES = [
    "INTAKING",
    "ANALYZING",
    "ANALYZE_ARTIFACT_CHECKING",
    "SPEC_LINT_RUNNING",
    "CHALLENGER_RUNNING",
    "DEV_CROSS_CHECK_RUNNING",
    "STAGING_TEST_RUNNING",
    "PR_CI_RUNNING",
    "ACCEPT_RUNNING",
    "ACCEPT_TEARING_DOWN",
    "REVIEW_RUNNING",
    "FIXER_RUNNING",
    "ARCHIVING",
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_name",
    [pytest.param(s, id=f"VLT-S15-{s}") for s in _SESSION_FAILED_STATE_NAMES],
)
async def test_vlt_s15_session_failed_self_loops_to_escalate(
    monkeypatch, state_name,
) -> None:
    """VLT-S15: (state, SESSION_FAILED) MUST dispatch escalate and remain in same state.
    Parametrized across all 13 in-flight states. Self-loop: no cleanup_runner."""
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    cas = _patch_io(monkeypatch)

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

    result = await _step(
        cur_state=cur,
        event=Event.SESSION_FAILED,
        tags=[_REQ_ID],
        ctx={},
    )
    await asyncio.sleep(0)

    assert result.get("action") == "escalate", (
        f"VLT-S15 [{state_name}]: action MUST be 'escalate'; got {result!r}"
    )
    assert result.get("next_state") == cur.value, (
        f"VLT-S15 [{state_name}]: SESSION_FAILED MUST be a self-loop (next_state == cur); "
        f"got next_state={result.get('next_state')!r}"
    )
    assert len(calls) == 1, (
        f"VLT-S15 [{state_name}]: escalate MUST be awaited exactly once; "
        f"called {len(calls)} time(s)"
    )
    cas_next = cas.call_args.args[3]
    assert cas_next == cur, (
        f"VLT-S15 [{state_name}]: CAS MUST commit self-loop (next_state == cur); "
        f"got {cas_next!r}"
    )
    assert cleanup_calls == [], (
        f"VLT-S15 [{state_name}]: SESSION_FAILED self-loop MUST NOT trigger cleanup; "
        f"got {cleanup_calls!r}"
    )


# ─── VLT-S16: INIT + SESSION_FAILED → skip, no escalate ─────────────────────


@pytest.mark.asyncio
async def test_vlt_s16_session_failed_on_init_is_dropped(monkeypatch) -> None:
    """VLT-S16: (INIT, SESSION_FAILED) → action='skip', no transition.
    INIT is not in the SESSION_FAILED transition set: escalate MUST NOT be dispatched."""
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    escalate_calls: list[int] = []

    async def _fake_escalate(**_kw):
        escalate_calls.append(1)
        return {}

    REGISTRY["escalate"] = _fake_escalate

    result = await _step(
        cur_state=ReqState.INIT,
        event=Event.SESSION_FAILED,
        tags=[_REQ_ID],
        ctx={},
    )

    assert result.get("action") == "skip", (
        f"VLT-S16: action MUST be 'skip'; got {result!r}"
    )
    reason = result.get("reason") or ""
    assert "no transition init+session.failed" in reason, (
        f"VLT-S16: reason MUST contain 'no transition init+session.failed'; got {reason!r}"
    )
    assert escalate_calls == [], (
        f"VLT-S16: escalate MUST NOT be dispatched on INIT + SESSION_FAILED; "
        f"got {len(escalate_calls)} call(s)"
    )
