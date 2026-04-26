"""Contract tests for REQ-code-quality-cleanup-1777220568.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-code-quality-cleanup-1777220568/specs/code-quality/spec.md

Scenarios covered:
  CQ-S1   ruff under project config reports zero findings on src + tests
  CQ-S2   vulture at 100% confidence reports zero findings on src
  CQ-S3   derive_event signature accepts exactly event_type and tags (no result_tags_only)

Testing strategy:
  - CQ-S1: real subprocess invocation of `uv run ruff check src/ tests/`
  - CQ-S2: real subprocess invocation of `uv tool run vulture src/ --min-confidence 100`
  - CQ-S3: subprocess Python one-liner to import and inspect derive_event signature,
    avoiding sys.path mutation inside the test process
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# orchestrator/tests/../../ = repo root; orchestrator/ is one level down from root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR_ROOT = REPO_ROOT / "orchestrator"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        cwd=str(ORCHESTRATOR_ROOT),
        capture_output=True,
        text=True,
    )


# ── CQ-S1 ───────────────────────────────────────────────────────────────────


def test_CQ_S1_ruff_check_src_and_tests_exits_0():
    """ruff check src/ tests/ under the project config exits 0 (all checks passed)."""
    result = _run("uv", "run", "ruff", "check", "src/", "tests/")
    assert result.returncode == 0, (
        f"ruff check src/ tests/ returned {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_CQ_S1_ruff_check_prints_all_checks_passed():
    """ruff check src/ tests/ prints 'All checks passed!' when there are no findings."""
    result = _run("uv", "run", "ruff", "check", "src/", "tests/")
    combined = result.stdout + result.stderr
    assert "All checks passed" in combined, (
        f"Expected 'All checks passed!' from ruff, got:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── CQ-S2 ───────────────────────────────────────────────────────────────────


def test_CQ_S2_vulture_100_confidence_exits_0():
    """vulture src/ --min-confidence 100 exits 0 with no fully-unused dead code."""
    result = _run("uv", "tool", "run", "vulture", "src/", "--min-confidence", "100")
    assert result.returncode == 0, (
        f"vulture src/ --min-confidence 100 returned {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_CQ_S2_vulture_100_confidence_produces_empty_stdout():
    """vulture src/ --min-confidence 100 produces empty stdout (no findings to report)."""
    result = _run("uv", "tool", "run", "vulture", "src/", "--min-confidence", "100")
    assert result.stdout.strip() == "", (
        f"Expected empty stdout from vulture at 100% confidence, got:\n{result.stdout}"
    )


# ── CQ-S3 ───────────────────────────────────────────────────────────────────


def test_CQ_S3_derive_event_has_no_result_tags_only_param():
    """derive_event signature does not contain the dead 'result_tags_only' parameter."""
    result = _run(
        "uv",
        "run",
        "python",
        "-c",
        (
            "import inspect, orchestrator.router; "
            "params = list(inspect.signature(orchestrator.router.derive_event).parameters.keys()); "
            "print(','.join(params))"
        ),
    )
    assert result.returncode == 0, (
        f"Failed to import orchestrator.router or inspect signature:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    params = [p for p in result.stdout.strip().split(",") if p]
    assert "result_tags_only" not in params, (
        f"Dead parameter 'result_tags_only' still present in derive_event: {params}"
    )


def test_CQ_S3_derive_event_has_exactly_event_type_and_tags():
    """derive_event signature contains exactly two params: event_type and tags."""
    result = _run(
        "uv",
        "run",
        "python",
        "-c",
        (
            "import inspect, orchestrator.router; "
            "params = list(inspect.signature(orchestrator.router.derive_event).parameters.keys()); "
            "print(','.join(params))"
        ),
    )
    assert result.returncode == 0, (
        f"Failed to inspect derive_event:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    params = [p for p in result.stdout.strip().split(",") if p]
    assert params == ["event_type", "tags"], (
        f"Expected derive_event params ['event_type', 'tags'], got: {params}"
    )
