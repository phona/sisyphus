"""Unit tests for scripts/lint-state-transitions.py (REQ-feat-silent-lint-376-v2).

Covers spec scenarios STPL-S1..S6:
  S1  Transition accepts progress kwarg with allowed values
  S2  Every existing self-loop transition has explicit progress annotation
  S3  Lint script exits zero on current TRANSITIONS table (snapshot)
  S4  Synthetic unannotated self-loop fails validation
  S5  Synthetic progress=yes self-loop fails validation
  S6  Unknown progress value fails validation

S7/S8 (Makefile + workflow YAML) live in test_contract_lint_state_transitions_ci.py
since they live outside the orchestrator package and would couple this module
to repo layout. Keep this file focused on the lint logic itself.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.state import TRANSITIONS, Event, ReqState, Transition

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LINT_SCRIPT = REPO_ROOT / "scripts" / "lint-state-transitions.py"


def _load_lint_module():
    """Import the lint script as a module without running its main()."""
    spec = importlib.util.spec_from_file_location("_lint_state_transitions", LINT_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─── STPL-S1: Transition accepts progress kwarg with allowed values ───────────
@pytest.mark.parametrize("value", ["yes", "no", "explicit-noop"])
def test_stpl_s1_transition_accepts_progress_values(value: str) -> None:
    t = Transition(next_state=ReqState.DONE, progress=value)
    assert t.progress == value


def test_stpl_s1_transition_default_progress_is_none() -> None:
    t = Transition(next_state=ReqState.DONE)
    assert t.progress is None


# ─── STPL-S2: every existing self-loop transition has explicit progress ────────
def test_stpl_s2_all_existing_self_loops_annotated() -> None:
    """Snapshot test: every (state, event) where next_state == src_state MUST
    have progress in {"no", "explicit-noop"}. New self-loops added without
    annotation will fail this test."""
    unannotated: list[str] = []
    for (src_state, event), trans in TRANSITIONS.items():
        if trans.next_state == src_state:
            if trans.progress not in ("no", "explicit-noop"):
                unannotated.append(
                    f"({src_state.value}, {event.value}) → {trans.next_state.value} "
                    f"progress={trans.progress!r}"
                )
    assert not unannotated, (
        "self-loop transitions without explicit progress annotation:\n  "
        + "\n  ".join(unannotated)
    )


def test_stpl_s2_known_self_loops_have_expected_annotation() -> None:
    """Specific snapshot for the two named transitions in the proposal."""
    assert TRANSITIONS[
        (ReqState.ESCALATED, Event.VERIFY_ESCALATE)
    ].progress == "explicit-noop"
    assert TRANSITIONS[
        (ReqState.REVIEW_RUNNING, Event.VERIFY_INFRA_RETRY)
    ].progress == "explicit-noop"
    # SESSION_FAILED dict comprehension entries
    for st in (
        ReqState.INTAKING, ReqState.ANALYZING, ReqState.SPEC_LINT_RUNNING,
        ReqState.STAGING_TEST_RUNNING, ReqState.PR_CI_RUNNING,
        ReqState.REVIEW_RUNNING, ReqState.FIXER_RUNNING,
    ):
        assert TRANSITIONS[(st, Event.SESSION_FAILED)].progress == "explicit-noop", (
            f"({st.value}, session.failed) missing explicit-noop annotation"
        )


# ─── STPL-S3: lint script exits zero on current TRANSITIONS table ─────────────
def test_stpl_s3_lint_script_exits_zero_on_real_transitions() -> None:
    proc = subprocess.run(
        [sys.executable, str(LINT_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"lint exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # Report content sanity
    assert "progress=yes" in proc.stdout
    assert "progress=explicit-noop" in proc.stdout
    assert "(escalated, verify.escalate)" in proc.stdout


# ─── STPL-S4: unannotated self-loop fails validation ──────────────────────────
def test_stpl_s4_unannotated_self_loop_violates() -> None:
    mod = _load_lint_module()
    fake = {
        (ReqState.DONE, Event.PR_MERGED): Transition(next_state=ReqState.DONE),
    }
    violations = mod.validate(fake)
    assert len(violations) == 1
    assert "(done, pr.merged)" in violations[0]
    assert "self-loop requires progress annotation" in violations[0]


# ─── STPL-S5: progress=yes self-loop fails validation ─────────────────────────
def test_stpl_s5_progress_yes_self_loop_violates() -> None:
    mod = _load_lint_module()
    fake = {
        (ReqState.INIT, Event.INTENT_ANALYZE): Transition(
            next_state=ReqState.INIT, progress="yes"
        ),
    }
    violations = mod.validate(fake)
    assert len(violations) == 1
    assert "progress=yes contradicts self-loop" in violations[0]


# ─── STPL-S6: unknown progress value fails validation ─────────────────────────
def test_stpl_s6_unknown_progress_value_violates() -> None:
    mod = _load_lint_module()
    fake = {
        (ReqState.INIT, Event.INTENT_ANALYZE): Transition(
            next_state=ReqState.ANALYZING, progress="maybe"
        ),
    }
    violations = mod.validate(fake)
    assert len(violations) == 1
    assert "unknown progress value" in violations[0]
    assert "'maybe'" in violations[0]


# ─── Bonus: progress=no/explicit-noop on advancing transition is contradiction ─
@pytest.mark.parametrize("value", ["no", "explicit-noop"])
def test_progress_no_on_advancing_transition_violates(value: str) -> None:
    mod = _load_lint_module()
    fake = {
        (ReqState.INIT, Event.INTENT_ANALYZE): Transition(
            next_state=ReqState.ANALYZING, progress=value
        ),
    }
    violations = mod.validate(fake)
    assert len(violations) == 1
    assert "contradicts advancing transition" in violations[0]


# ─── Bonus: empty / advancing-only dict yields no violations ──────────────────
def test_validate_returns_empty_for_clean_advancing_transitions() -> None:
    mod = _load_lint_module()
    fake = {
        (ReqState.INIT, Event.INTENT_ANALYZE): Transition(next_state=ReqState.ANALYZING),
        (ReqState.ANALYZING, Event.ANALYZE_DONE): Transition(
            next_state=ReqState.SPEC_LINT_RUNNING, progress="yes"
        ),
        (ReqState.ESCALATED, Event.VERIFY_ESCALATE): Transition(
            next_state=ReqState.ESCALATED, progress="explicit-noop"
        ),
    }
    assert mod.validate(fake) == []
