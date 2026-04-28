"""Contract tests for REQ-ci-lint-test-thanatos-fix-1777338398.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-ci-lint-test-thanatos-fix-1777338398/specs/thanatos-ci/spec.md

Scenarios covered:
  TCIF-S1   ci-integration-test exits 0 when no PostgreSQL is reachable
  TCIF-S1   ci-integration-test recipe does NOT invoke pytest inside thanatos/
  TCIF-S3   .github/workflows/thanatos-ci.yml exists with thanatos/** path triggers
  TCIF-S3   thanatos-ci.yml defines at least one job (produces GHA check-runs)
  TCIF-S4   thanatos/pyproject.toml declares [dependency-groups].dev with pytest
  TCIF-S4   ci-unit-test recipe runs pytest in thanatos/ with -m "not integration"

Testing strategy:
  - TCIF-S1 (behavioral): real subprocess make invocation with unreachable PostgreSQL DSN
  - TCIF-S1 (recipe): make --dry-run to verify thanatos block is absent from recipe
  - TCIF-S3: file existence + content assertion on thanatos-ci.yml
  - TCIF-S4: file content assertion on thanatos/pyproject.toml + make --dry-run ci-unit-test
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

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


# ── TCIF-S1 ─────────────────────────────────────────────────────────────────


def test_TCIF_S1_ci_integration_test_exits_0_without_postgres():
    """ci-integration-test exits 0 when PostgreSQL is not reachable."""
    result = _make(
        "ci-integration-test",
        env_extra={"SISYPHUS_PG_DSN": "postgresql://test:test@localhost:19999/test"},
    )
    assert result.returncode == 0, (
        f"ci-integration-test returned {result.returncode}; expected 0 when "
        f"PostgreSQL is unreachable.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_TCIF_S1_ci_integration_test_recipe_does_not_invoke_thanatos_pytest():
    """ci-integration-test recipe does NOT cd into thanatos/ to invoke pytest.

    The thanatos block was removed (Option B fix): thanatos integration tests are
    managed by the thanatos-ci.yml workflow, not the root Makefile.
    """
    result = _make("--dry-run", "ci-integration-test")
    combined = result.stdout + result.stderr
    thanatos_pytest_lines = [
        line
        for line in combined.splitlines()
        if "thanatos" in line and "pytest" in line
    ]
    assert not thanatos_pytest_lines, (
        "ci-integration-test recipe still invokes pytest inside thanatos/.\n"
        "Expected the thanatos block to be removed (Option B).\n"
        "Offending lines:\n" + "\n".join(thanatos_pytest_lines)
    )


# ── TCIF-S3 ─────────────────────────────────────────────────────────────────


def test_TCIF_S3_thanatos_ci_workflow_exists():
    """.github/workflows/thanatos-ci.yml exists so pr_ci_watch gets check-runs."""
    workflow = REPO_ROOT / ".github" / "workflows" / "thanatos-ci.yml"
    assert workflow.exists(), (
        f"Expected .github/workflows/thanatos-ci.yml to exist at {workflow}.\n"
        "Without this workflow, pr_ci_watch sees 'no-gha' for thanatos PRs."
    )


def test_TCIF_S3_thanatos_ci_triggers_on_pull_request_for_thanatos_paths():
    """thanatos-ci.yml triggers on pull_request with paths: thanatos/** filter."""
    workflow = REPO_ROOT / ".github" / "workflows" / "thanatos-ci.yml"
    assert workflow.exists(), (
        ".github/workflows/thanatos-ci.yml not found — cannot verify path triggers"
    )
    content = workflow.read_text()
    assert "pull_request" in content, (
        "Expected 'pull_request' trigger in thanatos-ci.yml.\n"
        f"File content:\n{content}"
    )
    assert "thanatos/**" in content, (
        "Expected 'thanatos/**' path filter in thanatos-ci.yml trigger.\n"
        "Without this, the workflow does not fire on thanatos PRs.\n"
        f"File content:\n{content}"
    )


def test_TCIF_S3_thanatos_ci_has_job_that_produces_check_runs():
    """thanatos-ci.yml defines at least one job so GHA produces check-runs."""
    workflow = REPO_ROOT / ".github" / "workflows" / "thanatos-ci.yml"
    assert workflow.exists(), (
        ".github/workflows/thanatos-ci.yml not found — cannot verify job definitions"
    )
    content = workflow.read_text()
    assert "jobs:" in content, (
        "Expected 'jobs:' section in thanatos-ci.yml.\n"
        "A workflow without jobs produces no check-runs, so pr_ci_watch still sees 'no-gha'.\n"
        f"File content:\n{content}"
    )


# ── TCIF-S4 ─────────────────────────────────────────────────────────────────


def test_TCIF_S4_thanatos_pyproject_has_dependency_groups_dev_with_pytest():
    """thanatos/pyproject.toml has [dependency-groups].dev containing pytest.

    Without this, 'uv run pytest' falls back to one-off tool mode which lacks
    the thanatos package, causing ModuleNotFoundError on import collection.
    """
    pyproject = REPO_ROOT / "thanatos" / "pyproject.toml"
    assert pyproject.exists(), "thanatos/pyproject.toml not found"
    content = pyproject.read_text()
    assert "[dependency-groups]" in content, (
        "Expected [dependency-groups] section in thanatos/pyproject.toml.\n"
        "Without this, 'uv run pytest' uses one-off tool mode and cannot import thanatos."
    )
    dep_groups_section = content.split("[dependency-groups]")[1].split("[")[0]
    assert "pytest" in dep_groups_section, (
        "Expected 'pytest' in [dependency-groups] section of thanatos/pyproject.toml.\n"
        f"[dependency-groups] content:\n{dep_groups_section}"
    )


def test_TCIF_S4_ci_unit_test_recipe_runs_pytest_in_thanatos_with_not_integration():
    """ci-unit-test recipe runs pytest -m 'not integration' inside thanatos/."""
    result = _make("--dry-run", "ci-unit-test")
    combined = result.stdout + result.stderr
    thanatos_lines = [line for line in combined.splitlines() if "thanatos" in line]
    assert thanatos_lines, (
        "Expected ci-unit-test recipe to invoke pytest in thanatos/ directory.\n"
        f"Dry-run output:\n{combined}"
    )
    assert any("not integration" in line for line in thanatos_lines), (
        "Expected ci-unit-test recipe to pass -m 'not integration' for thanatos pytest.\n"
        f"thanatos-related lines:\n" + "\n".join(thanatos_lines)
    )
