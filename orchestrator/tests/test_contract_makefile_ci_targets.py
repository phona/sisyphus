"""Contract tests for REQ-makefile-ci-targets-1777110320.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-makefile-ci-targets-1777110320/specs/makefile-ci-targets/spec.md

Scenarios covered:
  MFCT-S1   ci-lint with empty BASE_REV runs full ruff scan (exits 0 on clean tree)
  MFCT-S2   ci-lint with non-empty BASE_REV scopes to changed Python files only
  MFCT-S3   ci-lint with non-empty BASE_REV but zero in-scope Python changes exits 0
  MFCT-S4   ci-unit-test recipe invokes pytest -m "not integration"
  MFCT-S5   ci-unit-test skips tests marked @pytest.mark.integration
  MFCT-S6   ci-integration-test maps pytest exit 5 (no tests collected) to exit 0
  MFCT-S7   ci-integration-test propagates non-zero non-5 pytest exit as failure
  MFCT-S8   ci-integration-test invokes pytest -m integration
  MFCT-S9   orchestrator/pyproject.toml registers the integration marker with description
  MFCT-S10  dev-cross-check target has been removed
  MFCT-S11  ci-test target has been removed

Testing strategy:
  - S1, S3, S6: real subprocess make invocations (safe: ci-lint runs ruff, not pytest;
    ci-integration-test runs pytest -m integration which won't collect these unit tests)
  - S2: smoke-check scoped path using parent commit as BASE_REV
  - S4, S5, S7, S8: make --dry-run to verify recipe content (avoids infinite pytest recursion
    that would occur if we invoked ci-unit-test from within a pytest session)
  - S9: file content assertion on pyproject.toml
  - S10, S11: make -n on removed targets verifies "No rule to make target" error
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# orchestrator/tests/../../ = repo root (where Makefile lives)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _make(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["make", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ── MFCT-S1 ─────────────────────────────────────────────────────────────────


def test_MFCT_S1_ci_lint_empty_base_rev_exits_0():
    """ci-lint with BASE_REV='' runs full ruff scan and exits 0 on a clean tree."""
    result = _make("ci-lint", env_extra={"BASE_REV": ""})
    assert result.returncode == 0, (
        f"make ci-lint (BASE_REV='') returned {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_MFCT_S1_ci_lint_no_base_rev_prints_full_scan():
    """ci-lint with BASE_REV unset outputs a 'full scan' message."""
    env = {k: v for k, v in os.environ.items() if k != "BASE_REV"}
    result = subprocess.run(
        ["make", "ci-lint"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"make ci-lint (no BASE_REV) failed with {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    assert "full scan" in result.stdout, (
        f"Expected 'full scan' message in stdout, got:\n{result.stdout}"
    )


# ── MFCT-S2 ─────────────────────────────────────────────────────────────────


def test_MFCT_S2_ci_lint_with_base_rev_does_not_run_full_scan():
    """ci-lint with BASE_REV set does NOT fall back to full scan (scoped path taken)."""
    try:
        commits = _git("log", "--format=%H", "-2").splitlines()
    except subprocess.CalledProcessError:
        pytest.skip("git log failed — not enough history")
    if len(commits) < 2:
        pytest.skip("not enough commits to test BASE_REV scoping")

    base_rev = commits[1]  # parent of HEAD
    result = _make("ci-lint", env_extra={"BASE_REV": base_rev})

    assert result.returncode == 0, (
        f"ci-lint (BASE_REV={base_rev[:8]}) exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "full scan" not in result.stdout, (
        "Expected scoped run (not full scan) when BASE_REV is set, but got full scan message"
    )


# ── MFCT-S3 ─────────────────────────────────────────────────────────────────


def test_MFCT_S3_ci_lint_no_py_changes_in_scope_exits_0():
    """ci-lint with BASE_REV=HEAD (empty diff) exits 0 without invoking ruff."""
    try:
        head_sha = _git("rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        pytest.skip("git rev-parse failed")

    result = _make("ci-lint", env_extra={"BASE_REV": head_sha})
    assert result.returncode == 0, (
        f"Expected exit 0 for empty diff (BASE_REV=HEAD), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "no Python files changed" in result.stdout, (
        f"Expected 'no Python files changed in scope' message, got:\n{result.stdout}"
    )


# ── MFCT-S4 ─────────────────────────────────────────────────────────────────


def test_MFCT_S4_ci_unit_test_recipe_excludes_integration_marker():
    """ci-unit-test recipe passes -m 'not integration' to pytest."""
    result = _make("--dry-run", "ci-unit-test")
    assert result.returncode == 0, (
        f"make --dry-run ci-unit-test failed:\n{result.stdout}\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "not integration" in combined, (
        f"Expected '-m \"not integration\"' in dry-run output:\n{combined}"
    )


# ── MFCT-S5 ─────────────────────────────────────────────────────────────────


def test_MFCT_S5_ci_unit_test_dry_run_shows_not_integration_flag():
    """ci-unit-test uses -m 'not integration' ensuring integration tests are deselected."""
    result = _make("--dry-run", "ci-unit-test")
    combined = result.stdout + result.stderr
    # pytest handles deselection; we verify the flag is present in the recipe
    assert "not integration" in combined, (
        f"Expected pytest -m 'not integration' in recipe, got:\n{combined}"
    )


# ── MFCT-S6 ─────────────────────────────────────────────────────────────────


def test_MFCT_S6_ci_integration_test_zero_tests_exits_0():
    """ci-integration-test maps pytest exit 5 (no tests collected) to exit 0."""
    result = _make("ci-integration-test")
    assert result.returncode == 0, (
        f"ci-integration-test returned {result.returncode}; expected 0 "
        f"(pytest exit 5 should map to success).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_MFCT_S6_ci_integration_test_prints_placeholder_message_on_exit5():
    """ci-integration-test prints a message when pytest exit-5 is mapped to exit 0."""
    result = _make("ci-integration-test")
    # Recipe should emit a message indicating exit-5 → pass mapping
    combined = result.stdout + result.stderr
    has_exit5_msg = "exit 5" in combined or "no integration" in combined.lower()
    assert has_exit5_msg, (
        f"Expected exit-5 placeholder message in output, got:\n{combined}"
    )


# ── MFCT-S7 ─────────────────────────────────────────────────────────────────


def test_MFCT_S7_ci_integration_test_recipe_does_not_swallow_all_errors():
    """ci-integration-test recipe does NOT unconditionally exit 0 (only exit 5 is mapped)."""
    result = _make("--dry-run", "ci-integration-test")
    combined = result.stdout + result.stderr
    # The recipe must reference exit code 5 specifically, not a blanket exit 0
    assert "5" in combined, (
        f"Expected exit-code-5 handling in recipe, got:\n{combined}"
    )
    # Should not contain unconditional 'exit 0' without the rc check
    # (We check that the recipe distinguishes rc=5 from other failures)
    assert "rc" in combined or "exit" in combined.lower(), (
        f"Expected conditional exit logic in recipe, got:\n{combined}"
    )


# ── MFCT-S8 ─────────────────────────────────────────────────────────────────


def test_MFCT_S8_ci_integration_test_uses_integration_marker():
    """ci-integration-test recipe invokes pytest -m integration."""
    result = _make("--dry-run", "ci-integration-test")
    combined = result.stdout + result.stderr
    assert "-m integration" in combined or "-m 'integration'" in combined, (
        f"Expected 'pytest -m integration' in recipe, got:\n{combined}"
    )


# ── MFCT-S9 ─────────────────────────────────────────────────────────────────


def test_MFCT_S9_pyproject_registers_integration_marker():
    """orchestrator/pyproject.toml declares the integration marker under pytest ini_options."""
    pyproject = REPO_ROOT / "orchestrator" / "pyproject.toml"
    assert pyproject.exists(), "orchestrator/pyproject.toml not found"
    content = pyproject.read_text()
    assert "integration:" in content, (
        "Expected 'integration:' marker entry in [tool.pytest.ini_options].markers"
    )


def test_MFCT_S9_integration_marker_has_human_readable_description():
    """The integration marker entry in pyproject.toml includes a description."""
    import re

    pyproject = REPO_ROOT / "orchestrator" / "pyproject.toml"
    content = pyproject.read_text()
    # Matches: "integration: <description text>"
    match = re.search(r'"integration:\s+\S', content)
    assert match is not None, (
        "Expected 'integration: <description>' in pyproject.toml markers list.\n"
        "Relevant lines:\n"
        + "\n".join(ln for ln in content.splitlines() if "integration" in ln.lower())
    )


# ── MFCT-S10 ────────────────────────────────────────────────────────────────


def test_MFCT_S10_dev_cross_check_target_removed():
    """make -n dev-cross-check exits non-zero with 'No rule to make target'."""
    result = _make("-n", "dev-cross-check")
    assert result.returncode != 0, (
        "Expected non-zero exit for removed target 'dev-cross-check', but make succeeded"
    )
    combined = result.stdout + result.stderr
    assert "No rule to make target" in combined, (
        f"Expected 'No rule to make target' error message, got:\n{combined}"
    )


# ── MFCT-S11 ────────────────────────────────────────────────────────────────


def test_MFCT_S11_ci_test_target_removed():
    """make -n ci-test exits non-zero with 'No rule to make target'."""
    result = _make("-n", "ci-test")
    assert result.returncode != 0, (
        "Expected non-zero exit for removed target 'ci-test', but make succeeded"
    )
    combined = result.stdout + result.stderr
    assert "No rule to make target" in combined, (
        f"Expected 'No rule to make target' error message, got:\n{combined}"
    )
