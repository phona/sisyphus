"""Challenger contract tests for REQ-analyze-artifact-check-1777254586.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-analyze-artifact-check-1777254586/specs/analyze-artifact-check/spec.md

Written by: challenger-agent (M18 — independent of dev implementation)

Scenarios covered:
  AAC-S1  ANALYZING/ANALYZE_DONE → ANALYZE_ARTIFACT_CHECKING + create_analyze_artifact_check
          AND ANALYZE_ARTIFACT_CHECKING/PASS → SPEC_LINT_RUNNING + create_spec_lint
          AND create_analyze_artifact_check is callable via actions REGISTRY
  AAC-S2  ANALYZE_ARTIFACT_CHECKING/FAIL → REVIEW_RUNNING + invoke_verifier_for_analyze_artifact_check_fail
          + verifier _STAGES contains "analyze_artifact_check"
  AAC-S3  ANALYZE_ARTIFACT_CHECKING in SESSION_FAILED self-loop
  AAC-S4  build_cmd has /workspace/source missing/empty guards
  AAC-S5  build_cmd checks proposal/tasks/spec/checkbox literals + feat branch
  AAC-S6  build_cmd refuses 0 eligible repos + ends with `[ $fail -eq 0 ]`
  AAC-S7  create_analyze_artifact_check pass writes artifact_checks + emits PASS
  AAC-S8  create_analyze_artifact_check non-zero exit emits FAIL + still writes artifact_checks

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import pytest

from orchestrator.state import TRANSITIONS, Event, ReqState, decide

# ─── AAC-S1 ──────────────────────────────────────────────────────────────


def test_aac_s1_analyze_done_routes_to_artifact_check_then_spec_lint() -> None:
    """AAC-S1: (ANALYZING, ANALYZE_DONE) MUST route through ANALYZE_ARTIFACT_CHECKING
    instead of jumping straight to SPEC_LINT_RUNNING; the artifact check pass MUST
    then route to SPEC_LINT_RUNNING with create_spec_lint.
    """
    t1 = decide(ReqState.ANALYZING, Event.ANALYZE_DONE)
    assert t1 is not None, (
        "AAC-S1: (ANALYZING, ANALYZE_DONE) MUST be a registered transition"
    )
    assert t1.next_state is ReqState.ANALYZE_ARTIFACT_CHECKING, (
        "AAC-S1: ANALYZE_DONE MUST move into ANALYZE_ARTIFACT_CHECKING, not "
        "directly into SPEC_LINT_RUNNING. Got next_state="
        f"{t1.next_state.value!r}"
    )
    assert t1.action == "create_analyze_artifact_check", (
        "AAC-S1: ANALYZE_DONE transition action MUST be 'create_analyze_artifact_check'. "
        f"Got {t1.action!r}"
    )

    t2 = decide(ReqState.ANALYZE_ARTIFACT_CHECKING, Event.ANALYZE_ARTIFACT_CHECK_PASS)
    assert t2 is not None, (
        "AAC-S1: (ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS) MUST be "
        "a registered transition"
    )
    assert t2.next_state is ReqState.SPEC_LINT_RUNNING, (
        "AAC-S1: ANALYZE_ARTIFACT_CHECK_PASS MUST move to SPEC_LINT_RUNNING. "
        f"Got {t2.next_state.value!r}"
    )
    assert t2.action == "create_spec_lint", (
        "AAC-S1: ANALYZE_ARTIFACT_CHECK_PASS action MUST be 'create_spec_lint'. "
        f"Got {t2.action!r}"
    )

    # The orchestrator dispatches actions by name via REGISTRY; if
    # create_analyze_artifact_check is not registered, the state machine
    # would fail at runtime even though the transition is defined.
    from orchestrator.actions import REGISTRY
    assert "create_analyze_artifact_check" in REGISTRY, (
        "AAC-S1: 'create_analyze_artifact_check' MUST be registered in actions.REGISTRY "
        "so the orchestrator can dispatch it when ANALYZE_DONE fires"
    )


# ─── AAC-S2 ──────────────────────────────────────────────────────────────


def test_aac_s2_fail_routes_to_verifier_with_dedicated_handler() -> None:
    """AAC-S2: (ANALYZE_ARTIFACT_CHECKING, FAIL) → REVIEW_RUNNING +
    invoke_verifier_for_analyze_artifact_check_fail; the action is registered;
    _STAGES contains the new stage so invoke_verifier accepts it.
    """
    t = decide(ReqState.ANALYZE_ARTIFACT_CHECKING, Event.ANALYZE_ARTIFACT_CHECK_FAIL)
    assert t is not None, "AAC-S2: FAIL transition MUST be registered"
    assert t.next_state is ReqState.REVIEW_RUNNING, (
        f"AAC-S2: FAIL MUST go to REVIEW_RUNNING. Got {t.next_state.value!r}"
    )
    assert t.action == "invoke_verifier_for_analyze_artifact_check_fail", (
        f"AAC-S2: FAIL action MUST be the dedicated verifier handler. Got {t.action!r}"
    )

    from orchestrator.actions import REGISTRY
    assert "invoke_verifier_for_analyze_artifact_check_fail" in REGISTRY, (
        "AAC-S2: invoke_verifier_for_analyze_artifact_check_fail MUST be registered "
        "in the actions REGISTRY"
    )

    from orchestrator.actions._verifier import _STAGES
    assert "analyze_artifact_check" in _STAGES, (
        "AAC-S2: '_verifier._STAGES' MUST contain 'analyze_artifact_check' so "
        "invoke_verifier(stage='analyze_artifact_check', trigger='fail') is accepted"
    )


# ─── AAC-S3 ──────────────────────────────────────────────────────────────


def test_aac_s3_session_failed_self_loop_covers_new_state() -> None:
    """AAC-S3: ANALYZE_ARTIFACT_CHECKING MUST appear in the SESSION_FAILED self-loop
    so a runner-side crash during the artifact check is funneled through escalate
    rather than silently bouncing.
    """
    t = TRANSITIONS.get((ReqState.ANALYZE_ARTIFACT_CHECKING, Event.SESSION_FAILED))
    assert t is not None, (
        "AAC-S3: (ANALYZE_ARTIFACT_CHECKING, SESSION_FAILED) MUST be in TRANSITIONS"
    )
    assert t.next_state is ReqState.ANALYZE_ARTIFACT_CHECKING, (
        "AAC-S3: SESSION_FAILED self-loop MUST keep state on "
        "ANALYZE_ARTIFACT_CHECKING (action 'escalate' decides whether to truly "
        f"escalate). Got {t.next_state.value!r}"
    )
    assert t.action == "escalate", (
        f"AAC-S3: SESSION_FAILED self-loop action MUST be 'escalate'. Got {t.action!r}"
    )


# ─── AAC-S4 ──────────────────────────────────────────────────────────────


def _get_build_cmd(req_id: str = "REQ-X") -> str:
    """Black-box: call _build_cmd as the spec calls out."""
    from orchestrator.checkers import analyze_artifact_check as c
    return c._build_cmd(req_id)


def test_aac_s4_build_cmd_guards_workspace_source_missing_and_empty() -> None:
    """AAC-S4: empty-source guard mirrors spec_lint."""
    cmd = _get_build_cmd()
    assert "[ ! -d /workspace/source ]" in cmd, (
        "AAC-S4: cmd MUST guard /workspace/source missing"
    )
    assert "FAIL analyze-artifact-check: /workspace/source missing" in cmd, (
        "AAC-S4: cmd MUST emit a FAIL marker when /workspace/source is missing"
    )
    assert "find /workspace/source -mindepth 1 -maxdepth 1 -type d" in cmd, (
        "AAC-S4: cmd MUST count repos under /workspace/source via find"
    )
    assert '"$repo_count" -eq 0' in cmd, (
        "AAC-S4: cmd MUST exit when repo_count == 0"
    )
    assert "FAIL analyze-artifact-check: /workspace/source empty" in cmd, (
        "AAC-S4: cmd MUST emit a FAIL marker on empty /workspace/source"
    )


# ─── AAC-S5 ──────────────────────────────────────────────────────────────


def test_aac_s5_build_cmd_checks_proposal_tasks_spec_checkbox_feat_branch() -> None:
    """AAC-S5: cmd references proposal.md / tasks.md / spec.md / checkbox regex /
    feat branch fetch literals.
    """
    cmd = _get_build_cmd("REQ-X")
    assert "openspec/changes/REQ-X/proposal.md" in cmd, (
        "AAC-S5: cmd MUST reference openspec/changes/REQ-X/proposal.md"
    )
    assert "openspec/changes/REQ-X/tasks.md" in cmd, (
        "AAC-S5: cmd MUST reference openspec/changes/REQ-X/tasks.md"
    )
    # spec.md is searched recursively under specs/
    assert '"$ch/specs"' in cmd and "spec.md" in cmd, (
        "AAC-S5: cmd MUST recursively probe specs/<capability>/spec.md inside the "
        "openspec/changes/<REQ>/ directory"
    )
    # Markdown checkbox char class fed to grep -E
    assert r"\[[ xX]\]" in cmd, (
        "AAC-S5: cmd MUST include a Markdown checkbox regex with class [ xX]"
    )
    assert "grep -E" in cmd, (
        "AAC-S5: cmd MUST use 'grep -E' for the checkbox check"
    )
    assert 'git fetch origin "feat/REQ-X"' in cmd, (
        "AAC-S5: cmd MUST fetch origin feat/REQ-X to obtain the analyze branch"
    )


# ─── AAC-S6 ──────────────────────────────────────────────────────────────


def test_aac_s6_build_cmd_refuses_zero_eligible_repos_and_aggregates_failure() -> None:
    """AAC-S6: ran=0 guard + final aggregated `[ $fail -eq 0 ]`."""
    cmd = _get_build_cmd()
    assert "ran=0" in cmd, "AAC-S6: cmd MUST initialise ran counter to 0"
    assert "ran=$((ran+1))" in cmd, (
        "AAC-S6: cmd MUST increment ran for each eligible repo"
    )
    assert '"$ran" -eq 0' in cmd, (
        "AAC-S6: cmd MUST guard ran==0 so 0-eligible-repos is a failure"
    )
    assert "0 source repos eligible" in cmd, (
        "AAC-S6: cmd MUST emit '0 source repos eligible' marker (mirrors spec_lint)"
    )
    assert cmd.rstrip().endswith("[ $fail -eq 0 ]"), (
        "AAC-S6: cmd MUST end with '[ $fail -eq 0 ]' so the final exit reflects "
        "the aggregated check status"
    )


# ─── AAC-S7 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aac_s7_action_pass_writes_artifact_then_emits_pass(monkeypatch) -> None:
    """AAC-S7: pass result MUST insert into artifact_checks with stage
    'analyze-artifact-check' AND return dict containing emit=PASS event.
    """
    from orchestrator.actions import create_analyze_artifact_check as mod
    from orchestrator.checkers._types import CheckResult

    fake_result = CheckResult(
        passed=True, exit_code=0,
        stdout_tail="ok\n", stderr_tail="",
        duration_sec=1.5, cmd="<<cmd>>",
    )

    async def fake_run(req_id, *, timeout_sec=120):
        return fake_result

    monkeypatch.setattr(mod.checker, "run_analyze_artifact_check", fake_run)

    insert_calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        insert_calls.append((req_id, stage, result))

    monkeypatch.setattr(mod.artifact_checks, "insert_check", fake_insert)

    class FakePool:
        async def execute(self, sql, *args):
            return None

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(mod.db, "get_pool", lambda: FakePool())

    body = type("B", (), {
        "issueId": "x", "projectId": "p", "event": "session.completed",
        "title": "T", "tags": [], "issueNumber": None,
    })()
    out = await mod.create_analyze_artifact_check(
        body=body, req_id="REQ-7", tags=[], ctx={},
    )
    assert out["emit"] == "analyze-artifact-check.pass", (
        "AAC-S7: emit MUST be analyze-artifact-check.pass on success"
    )
    assert len(insert_calls) == 1, (
        "AAC-S7: artifact_checks.insert_check MUST be called exactly once on success"
    )
    assert insert_calls[0][1] == "analyze-artifact-check", (
        f"AAC-S7: artifact_checks stage MUST be 'analyze-artifact-check'. "
        f"Got {insert_calls[0][1]!r}"
    )
    assert insert_calls[0][2].passed is True


# ─── AAC-S8 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aac_s8_action_nonzero_emits_fail_with_exit_code(monkeypatch) -> None:
    """AAC-S8: non-zero exit MUST emit FAIL with the same exit_code AND still
    write a row to artifact_checks for observability.
    """
    from orchestrator.actions import create_analyze_artifact_check as mod
    from orchestrator.checkers._types import CheckResult

    fake_result = CheckResult(
        passed=False, exit_code=1,
        stdout_tail="",
        stderr_tail="=== FAIL analyze-artifact-check: ... ===\n",
        duration_sec=0.5, cmd="<<cmd>>",
    )

    async def fake_run(req_id, *, timeout_sec=120):
        return fake_result

    monkeypatch.setattr(mod.checker, "run_analyze_artifact_check", fake_run)

    insert_calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        insert_calls.append((req_id, stage, result))

    monkeypatch.setattr(mod.artifact_checks, "insert_check", fake_insert)

    class FakePool:
        async def execute(self, sql, *args):
            return None

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(mod.db, "get_pool", lambda: FakePool())

    body = type("B", (), {
        "issueId": "x", "projectId": "p", "event": "session.completed",
        "title": "T", "tags": [], "issueNumber": None,
    })()
    out = await mod.create_analyze_artifact_check(
        body=body, req_id="REQ-8", tags=[], ctx={},
    )
    assert out["emit"] == "analyze-artifact-check.fail", (
        "AAC-S8: emit MUST be analyze-artifact-check.fail on non-zero exit"
    )
    assert out["passed"] is False
    assert out["exit_code"] == 1
    assert len(insert_calls) == 1, (
        "AAC-S8: artifact_checks.insert_check MUST still be called once on FAIL"
    )
    assert insert_calls[0][1] == "analyze-artifact-check"
