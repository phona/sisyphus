"""Contract tests for state-transition-progress-lint (REQ-feat-silent-lint-376-v2-1777866643).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-feat-silent-lint-376-v2-1777866643/specs/state-transition-progress-lint/spec.md

Scenarios covered:
  STPL-S1  Transition accepts progress kwarg with allowed values + frozen dataclass
  STPL-S2  every existing self-loop transition has explicit progress annotation
  STPL-S3  lint script exits zero on current TRANSITIONS table + report substrings
  STPL-S4  lint flags an unannotated self-loop
  STPL-S5  lint flags a contradictory progress=yes self-loop
  STPL-S6  lint flags an unknown progress value
  STPL-S7  ci-lint target invokes lint-state-transitions.py
  STPL-S8  orchestrator-ci.yml runs the lint script in lint-test job
"""
from __future__ import annotations

import dataclasses
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from orchestrator.state import TRANSITIONS, Event, ReqState, Transition

REPO_ROOT = Path(__file__).resolve().parents[2]
LINT_SCRIPT = REPO_ROOT / "scripts" / "lint-state-transitions.py"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _load_lint_module():
    """Load scripts/lint-state-transitions.py as an importable module for introspection."""
    assert LINT_SCRIPT.exists(), f"missing lint script at {LINT_SCRIPT}"
    spec = importlib.util.spec_from_file_location(
        "_lint_state_transitions_under_test", LINT_SCRIPT
    )
    assert spec is not None and spec.loader is not None, f"could not load {LINT_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        # tolerate scripts that auto-run + sys.exit on import; we only want the namespace
        pass
    return module


_VALIDATE_NAME_CANDIDATES = (
    "validate",
    "validate_transitions",
    "lint",
    "lint_transitions",
    "check",
    "check_transitions",
    "find_violations",
    "collect_violations",
)


def _get_validate_fn(module):
    """Resolve the spec's 'validation function' on the lint module.

    The spec mandates a callable that takes a TRANSITIONS-shaped dict and returns
    a list of violations, but does not pin a name. Try common names first; fall
    back to scanning public callables that accept one positional arg and return
    a list when given an empty dict.
    """
    for name in _VALIDATE_NAME_CANDIDATES:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    for name in dir(module):
        if name.startswith("_"):
            continue
        fn = getattr(module, name)
        if not callable(fn):
            continue
        try:
            result = fn({})
        except Exception:
            continue
        if isinstance(result, list):
            return fn
    pytest.fail(
        f"could not locate validation function on {LINT_SCRIPT.name}; "
        f"tried names {_VALIDATE_NAME_CANDIDATES} and a public-callable scan"
    )


def _violations_blob(violations) -> str:
    """Coerce a list of violations (str / tuple / dict / dataclass / ...) into a single
    searchable string so we can match spec-mandated wording without locking in a shape."""
    return " | ".join(str(v) for v in violations)


# ─── STPL-S1 ──────────────────────────────────────────────────────────────────


def test_stpl_s1_transition_accepts_progress_kwarg_with_allowed_values():
    t_default = Transition(next_state=ReqState.DONE)
    assert t_default.progress is None, "omitting progress must default to None"

    for value in ("yes", "no", "explicit-noop"):
        t = Transition(next_state=ReqState.DONE, progress=value)
        assert t.progress == value, f"progress={value!r} not preserved"


def test_stpl_s1_transition_dataclass_is_frozen():
    assert dataclasses.is_dataclass(Transition), "Transition must be a dataclass"
    params = getattr(Transition, "__dataclass_params__", None)
    assert params is not None and params.frozen, "Transition must remain a frozen dataclass"


# ─── STPL-S2 ──────────────────────────────────────────────────────────────────


def test_stpl_s2_every_self_loop_has_explicit_progress_annotation():
    offenders = []
    for (src_state, event), transition in TRANSITIONS.items():
        if transition.next_state != src_state:
            continue
        if transition.progress not in ("no", "explicit-noop"):
            offenders.append(((src_state, event), transition.progress))
    assert not offenders, (
        "every self-loop transition must have progress in {'no','explicit-noop'}; "
        f"offenders: {offenders}"
    )


def test_stpl_s2_escalated_verify_escalate_is_explicit_noop():
    t = TRANSITIONS[(ReqState.ESCALATED, Event.VERIFY_ESCALATE)]
    assert t.progress == "explicit-noop"


def test_stpl_s2_review_running_verify_infra_retry_is_explicit_noop():
    t = TRANSITIONS[(ReqState.REVIEW_RUNNING, Event.VERIFY_INFRA_RETRY)]
    assert t.progress == "explicit-noop"


def test_stpl_s2_all_session_failed_entries_are_explicit_noop():
    offenders = []
    for (src_state, event), transition in TRANSITIONS.items():
        if event != Event.SESSION_FAILED:
            continue
        if transition.progress != "explicit-noop":
            offenders.append((src_state, transition.progress))
    assert not offenders, (
        "every (state, SESSION_FAILED) entry must have progress=='explicit-noop'; "
        f"offenders: {offenders}"
    )


# ─── STPL-S3 ──────────────────────────────────────────────────────────────────


def test_stpl_s3_lint_script_exits_zero_on_current_transitions_table():
    assert LINT_SCRIPT.exists(), f"missing lint script at {LINT_SCRIPT}"
    proc = subprocess.run(
        [sys.executable, str(LINT_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"lint must exit 0 on current TRANSITIONS, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    out = proc.stdout
    assert "progress=yes" in out, f"stdout missing 'progress=yes' bucket header: {out!r}"
    assert "progress=explicit-noop" in out, (
        f"stdout missing 'progress=explicit-noop' bucket header: {out!r}"
    )
    assert "(escalated, verify.escalate) → escalated" in out, (
        "stdout must list '(escalated, verify.escalate) → escalated' under explicit-noop "
        f"bucket; got:\n{out}"
    )


# ─── STPL-S4 ──────────────────────────────────────────────────────────────────


def test_stpl_s4_lint_flags_unannotated_self_loop():
    module = _load_lint_module()
    validate = _get_validate_fn(module)
    fake = {
        (ReqState.DONE, Event.PR_MERGED): Transition(next_state=ReqState.DONE),
    }
    violations = validate(fake)
    assert isinstance(violations, list) and violations, (
        f"validate() must return non-empty list for unannotated self-loop; got {violations!r}"
    )
    blob = _violations_blob(violations).lower()
    assert "done" in blob and "pr.merged" in blob, (
        f"violation must identify (done, pr.merged) pair; got {violations!r}"
    )
    assert "self-loop requires progress annotation" in blob, (
        "violation message must mention 'self-loop requires progress annotation'; "
        f"got {violations!r}"
    )


# ─── STPL-S5 ──────────────────────────────────────────────────────────────────


def test_stpl_s5_lint_flags_contradictory_progress_yes_self_loop():
    module = _load_lint_module()
    validate = _get_validate_fn(module)
    fake = {
        (ReqState.INIT, Event.INTENT_ANALYZE): Transition(
            next_state=ReqState.INIT, progress="yes"
        ),
    }
    violations = validate(fake)
    assert isinstance(violations, list) and violations, (
        f"validate() must return non-empty list for progress=yes self-loop; got {violations!r}"
    )
    blob = _violations_blob(violations).lower()
    assert "progress=yes contradicts self-loop" in blob, (
        "violation must reference 'progress=yes contradicts self-loop'; "
        f"got {violations!r}"
    )


# ─── STPL-S6 ──────────────────────────────────────────────────────────────────


def test_stpl_s6_lint_flags_unknown_progress_value():
    module = _load_lint_module()
    validate = _get_validate_fn(module)
    # Use an advancing pair (INIT → ANALYZING) so we isolate the "unknown progress
    # value" rule from the self-loop rules.
    fake = {
        (ReqState.INIT, Event.INTENT_ANALYZE): Transition(
            next_state=ReqState.ANALYZING, progress="maybe"
        ),
    }
    violations = validate(fake)
    assert isinstance(violations, list) and violations, (
        f"validate() must return non-empty list for unknown progress value; got {violations!r}"
    )
    blob = _violations_blob(violations)
    assert "maybe" in blob, (
        f"violation must reference the invalid value 'maybe'; got {violations!r}"
    )


# ─── STPL-S7 ──────────────────────────────────────────────────────────────────


def test_stpl_s7_ci_lint_target_invokes_lint_state_transitions_py():
    if shutil.which("make") is None:
        pytest.skip("make not on PATH; STPL-S7 verifies the Makefile contract")
    proc = subprocess.run(
        ["make", "-n", "ci-lint"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"`make -n ci-lint` failed: stderr={proc.stderr!r}"
    assert "lint-state-transitions.py" in proc.stdout, (
        "`make -n ci-lint` recipe must reference scripts/lint-state-transitions.py; "
        f"got:\n{proc.stdout}"
    )


# ─── STPL-S8 ──────────────────────────────────────────────────────────────────


def test_stpl_s8_orchestrator_ci_yml_runs_lint_script():
    workflow = REPO_ROOT / ".github" / "workflows" / "orchestrator-ci.yml"
    assert workflow.exists(), f"missing workflow at {workflow}"
    data = yaml.safe_load(workflow.read_text())
    jobs = (data or {}).get("jobs") or {}
    lint_test = jobs.get("lint-test") or {}
    steps = lint_test.get("steps") or []
    matched = [
        step
        for step in steps
        if isinstance(step, dict) and "scripts/lint-state-transitions.py" in (step.get("run") or "")
    ]
    assert matched, (
        "jobs.lint-test.steps must include at least one step whose `run` references "
        f"scripts/lint-state-transitions.py; got steps: {steps!r}"
    )
