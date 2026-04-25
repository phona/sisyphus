"""Contract tests for REQ-checker-empty-source-1777113775.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-checker-empty-source-1777113775/specs/checker-empty-source-guard/spec.md
  openspec/changes/REQ-checker-empty-source-1777113775/specs/checker-empty-source-guard/contract.spec.yaml

Scenarios covered:
  CESG-S1  spec_lint exits non-zero when /workspace/source is missing
  CESG-S2  spec_lint exits non-zero when /workspace/source has zero subdirectories
  CESG-S3  spec_lint exits non-zero when no repo has feat/<REQ> + openspec/changes/<REQ>/
  CESG-S4  dev_cross_check exits non-zero when /workspace/source is missing
  CESG-S5  dev_cross_check exits non-zero when /workspace/source is empty
  CESG-S6  dev_cross_check exits non-zero when no repo has feat/<REQ> + ci-lint target
  CESG-S7  staging_test exits non-zero when /workspace/source is missing
  CESG-S8  staging_test exits non-zero when /workspace/source is empty
  CESG-S9  staging_test exits non-zero when no repo has both ci-unit-test and ci-integration-test

Testing strategy:
  - S1, S2, S4, S5, S7, S8: Run the generated shell with a controlled tmpdir; guards A and B
    fire before any git/docker work, so no external services needed.
  - S3, S6, S9 (behavioral): Create a real git repo in tmpdir (no remote); git fetch origin
    fails, triggering the skip path; ran stays 0; Guard C fires. Matches spec's "real git repo
    that has no feat/<REQ> branch on origin" precondition.
  - stderr_format: contract.spec.yaml requires the full literal pattern
    '=== FAIL <stage>: <reason> — refusing to silent-pass ===' — asserted separately.
"""
from __future__ import annotations

import subprocess

import pytest

from orchestrator.checkers.dev_cross_check import _build_cmd as build_dev_cross_check_cmd
from orchestrator.checkers.spec_lint import _build_cmd as build_spec_lint_cmd
from orchestrator.checkers.staging_test import _build_cmd as build_staging_test_cmd

_BUILDERS = [
    pytest.param(build_spec_lint_cmd, "spec_lint", id="spec_lint"),
    pytest.param(build_dev_cross_check_cmd, "dev_cross_check", id="dev_cross_check"),
    pytest.param(build_staging_test_cmd, "staging_test", id="staging_test"),
]

REQ_ID = "REQ-cesg-contract-test"


def _patched_cmd(builder, fake_root: str) -> str:
    """Replace /workspace/source with a controlled tmpdir path."""
    return builder(REQ_ID).replace("/workspace/source", fake_root)


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=15
    )


# ── Guard A: /workspace/source missing ────────────────────────────────────────
# Scenarios CESG-S1, CESG-S4, CESG-S7


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_cmd_exits_nonzero_when_source_dir_missing(builder, name, tmp_path):
    """CESG-S1/S4/S7: exit 1 + stderr 'FAIL <stage>: ... missing' when /workspace/source absent."""
    fake_root = str(tmp_path / "source")  # intentionally not created
    r = _run(_patched_cmd(builder, fake_root))
    assert r.returncode != 0, (
        f"{name} should fail when source dir missing, got rc={r.returncode}"
    )
    assert f"FAIL {name}" in r.stderr, (
        f"{name}: expected 'FAIL {name}' in stderr.\nstderr: {r.stderr}"
    )
    assert "missing" in r.stderr, (
        f"{name}: expected 'missing' in stderr.\nstderr: {r.stderr}"
    )


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_guard_a_stderr_contains_refusing_to_silent_pass(builder, name, tmp_path):
    """CESG-S1/S4/S7 (format): stderr MUST contain 'refusing to silent-pass' per contract.spec.yaml."""
    fake_root = str(tmp_path / "source")
    r = _run(_patched_cmd(builder, fake_root))
    assert "refusing to silent-pass" in r.stderr, (
        f"{name}: contract.spec.yaml requires stderr to contain 'refusing to silent-pass'.\n"
        f"stderr: {r.stderr}"
    )


# ── Guard B: /workspace/source exists but zero subdirectories ─────────────────
# Scenarios CESG-S2, CESG-S5, CESG-S8


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_cmd_exits_nonzero_when_source_dir_empty(builder, name, tmp_path):
    """CESG-S2/S5/S8: exit 1 + stderr 'FAIL <stage>: ... empty' when source has 0 subdirs."""
    fake_root = tmp_path / "source"
    fake_root.mkdir()
    r = _run(_patched_cmd(builder, str(fake_root)))
    assert r.returncode != 0, (
        f"{name} should fail when source dir empty, got rc={r.returncode}"
    )
    assert f"FAIL {name}" in r.stderr, (
        f"{name}: expected 'FAIL {name}' in stderr.\nstderr: {r.stderr}"
    )
    assert "empty" in r.stderr, (
        f"{name}: expected 'empty' in stderr.\nstderr: {r.stderr}"
    )


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_guard_b_stderr_contains_refusing_to_silent_pass(builder, name, tmp_path):
    """CESG-S2/S5/S8 (format): stderr MUST contain 'refusing to silent-pass' per contract.spec.yaml."""
    fake_root = tmp_path / "source"
    fake_root.mkdir()
    r = _run(_patched_cmd(builder, str(fake_root)))
    assert "refusing to silent-pass" in r.stderr, (
        f"{name}: contract.spec.yaml requires stderr to contain 'refusing to silent-pass'.\n"
        f"stderr: {r.stderr}"
    )


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_guard_b_source_dir_with_only_files_not_subdirs_is_empty(builder, name, tmp_path):
    """Guard B must count only subdirectories; a source dir with only files counts as empty."""
    fake_root = tmp_path / "source"
    fake_root.mkdir()
    (fake_root / "stale.lock").write_text("stale")  # file, not a subdir
    r = _run(_patched_cmd(builder, str(fake_root)))
    assert r.returncode != 0, (
        f"{name} should treat source dir with only files as empty, got rc={r.returncode}"
    )
    assert "empty" in r.stderr, (
        f"{name}: expected 'empty' in stderr (files-only dir).\nstderr: {r.stderr}"
    )


# ── Guard C: 0 eligible repos ─────────────────────────────────────────────────
# Scenarios CESG-S3, CESG-S6, CESG-S9


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_cmd_exits_nonzero_when_no_repo_eligible(builder, name, tmp_path):
    """CESG-S3/S6/S9: exit 1 when a repo subdir exists but has no feat/<REQ> branch."""
    fake_root = tmp_path / "source"
    (fake_root / "repo-a").mkdir(parents=True)
    r = _run(_patched_cmd(builder, str(fake_root)))
    assert r.returncode != 0, (
        f"{name} should fail when 0 repos eligible, got rc={r.returncode}"
    )
    assert "0 source repos eligible" in r.stderr, (
        f"{name}: expected '0 source repos eligible' in stderr.\nstderr: {r.stderr}"
    )


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_guard_c_real_git_repo_without_feat_branch(builder, name, tmp_path):
    """CESG-S3/S6/S9 (real git): a real git repo with no remote → fetch fails → ran stays 0.

    Spec precondition: '/workspace/source/repo-a exists (real git repo) but has no
    feat/<REQ> branch on origin'. This test uses a git init'd repo without any remote
    so that `git fetch origin feat/<REQ>` exits non-zero, triggering the skip path.
    """
    fake_root = tmp_path / "source"
    repo_dir = fake_root / "repo-a"
    repo_dir.mkdir(parents=True)
    subprocess.run(
        ["git", "init", str(repo_dir)],
        capture_output=True, check=True,
    )
    r = _run(_patched_cmd(builder, str(fake_root)))
    assert r.returncode != 0, (
        f"{name}: real git repo with no remote should cause Guard C to fire.\n"
        f"rc={r.returncode}\nstderr: {r.stderr}"
    )
    assert "0 source repos eligible" in r.stderr, (
        f"{name}: expected '0 source repos eligible'.\nstderr: {r.stderr}"
    )


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_guard_c_stderr_contains_refusing_to_silent_pass(builder, name, tmp_path):
    """CESG-S3/S6/S9 (format): Guard C stderr MUST contain 'refusing to silent-pass'."""
    fake_root = tmp_path / "source"
    (fake_root / "repo-a").mkdir(parents=True)
    r = _run(_patched_cmd(builder, str(fake_root)))
    assert "refusing to silent-pass" in r.stderr, (
        f"{name}: contract.spec.yaml requires 'refusing to silent-pass' in Guard C stderr.\n"
        f"stderr: {r.stderr}"
    )
