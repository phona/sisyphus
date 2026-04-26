"""Contract tests for REQ-checker-no-feat-branch-fail-loud-1777123726.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-checker-no-feat-branch-fail-loud-1777123726/specs/
    checker-empty-source-guard/spec.md (delta: MODIFIED Requirement)

Background: REQ-checker-empty-source-1777113775 added Guards A/B/C, but Guard
C only fires when *every* cloned repo is silent-skipped. If 2+ repos are
cloned and only some lack feat/<REQ>, the missing ones were silently skipped
and the checker passed on the remainder — masking analyze-agent's failure to
push to a declared-involved repo.

This REQ tightens dev_cross_check and staging_test (NOT spec_lint, because
spec changes may legitimately consolidate in a single spec_home repo): if a
cloned repo lacks feat/<REQ>, the checker now sets fail=1 and emits a
'has no feat/<REQ> branch reachable on origin — refusing to silent-pass' line to
stderr, instead of '[skip] $name: no feat branch / not involved'.

Scenarios:
  CNFB-S1  dev_cross_check fails loud when single cloned repo has no feat branch
  CNFB-S2  staging_test fails loud when single cloned repo has no feat branch
  CNFB-S3  dev_cross_check Guard C still fires when feat branch present but no ci-lint target
  CNFB-S4  staging_test Guard C still fires when feat branch present but missing make targets
  CNFB-S5  spec_lint behavior is unchanged (no new fail-loud emission)
  CNFB-S6  fail-loud message names the offending repo (per-repo attribution)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.checkers.dev_cross_check import _build_cmd as build_dev_cross_check_cmd
from orchestrator.checkers.spec_lint import _build_cmd as build_spec_lint_cmd
from orchestrator.checkers.staging_test import _build_cmd as build_staging_test_cmd

REQ_ID = "REQ-cnfb-contract-test"


def _patched_cmd(builder, fake_root: str) -> str:
    return builder(REQ_ID).replace("/workspace/source", fake_root)


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=15
    )


def _make_repo_with_feat_branch(parent: Path, name: str, req_id: str, makefile: str | None = None) -> Path:
    """Create $parent/source/$name backed by a bare origin that has main + feat/<req_id>.

    git fetch origin feat/<req_id> succeeds, so the checker reaches the post-fetch
    Makefile-target check (which is what Guard C now exclusively gates).
    """
    bare = parent / f"{name}.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    src = parent / "source" / name
    src.mkdir(parents=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(parent), "PATH": "/usr/bin:/bin"}
    run_kw = {"check": True, "capture_output": True, "env": env, "cwd": str(src)}

    subprocess.run(["git", "init", "-b", "main", "."], **run_kw)
    (src / "README").write_text("init\n")
    if makefile is not None:
        (src / "Makefile").write_text(makefile)
    subprocess.run(["git", "add", "."], **run_kw)
    subprocess.run(["git", "commit", "-m", "init"], **run_kw)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], **run_kw)
    subprocess.run(["git", "push", "-u", "origin", "main"], **run_kw)
    subprocess.run(["git", "checkout", "-b", f"feat/{req_id}"], **run_kw)
    (src / "feat-marker").write_text("feat\n")
    subprocess.run(["git", "add", "."], **run_kw)
    subprocess.run(["git", "commit", "-m", "feat work"], **run_kw)
    subprocess.run(["git", "push", "-u", "origin", f"feat/{req_id}"], **run_kw)
    subprocess.run(["git", "checkout", "main"], **run_kw)
    return src


def _make_repo_without_feat_branch(parent: Path, name: str) -> Path:
    """Create $parent/source/$name as a real git repo with no remote.

    `git fetch origin feat/<REQ>` exits non-zero, exercising the per-repo
    no-feat-branch fail-loud path on dev_cross_check / staging_test.
    """
    src = parent / "source" / name
    src.mkdir(parents=True)
    subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
    return src


# ── CNFB-S1 / CNFB-S2: per-repo fail-loud on missing feat branch ─────────────


@pytest.mark.parametrize(
    "builder,name,fail_substring",
    [
        pytest.param(
            build_dev_cross_check_cmd,
            "dev_cross_check",
            f"FAIL dev_cross_check: repo-a has no feat/{REQ_ID} branch reachable on origin",
            id="dev_cross_check",
        ),
        pytest.param(
            build_staging_test_cmd,
            "staging_test",
            f"FAIL staging_test: repo-a has no feat/{REQ_ID} branch reachable on origin",
            id="staging_test",
        ),
    ],
)
def test_per_repo_no_feat_branch_fails_loud(builder, name, fail_substring, tmp_path):
    """CNFB-S1/S2: a cloned repo without feat/<REQ> on origin → fail=1 + fail-loud stderr."""
    fake_root = tmp_path / "source"
    _make_repo_without_feat_branch(tmp_path, "repo-a")
    r = _run(_patched_cmd(builder, str(fake_root)))
    assert r.returncode != 0, (
        f"{name}: missing feat branch on cloned repo MUST cause non-zero exit.\n"
        f"rc={r.returncode}\nstderr: {r.stderr}"
    )
    assert fail_substring in r.stderr, (
        f"{name}: stderr MUST contain per-repo fail-loud line.\n"
        f"expected substring: {fail_substring!r}\nstderr: {r.stderr}"
    )
    assert "refusing to silent-pass" in r.stderr, (
        f"{name}: fail-loud stderr MUST carry 'refusing to silent-pass'.\n"
        f"stderr: {r.stderr}"
    )
    # The old silent-skip line MUST NOT appear — the whole point is to stop
    # masking the failure with [skip] noise.
    assert "no feat branch / not involved" not in r.stderr, (
        f"{name}: deprecated [skip] silent-pass line leaked through.\nstderr: {r.stderr}"
    )
    assert "no feat branch / not involved" not in r.stdout, (
        f"{name}: deprecated [skip] silent-pass line leaked through.\nstdout: {r.stdout}"
    )


# ── CNFB-S3 / CNFB-S4: Guard C still gates Makefile-target-missing ───────────


def test_dev_cross_check_guard_c_fires_when_feat_branch_present_but_no_ci_lint_target(tmp_path):
    """CNFB-S3 (≡ CESG-S6 done correctly): feat/<REQ> branch present + no ci-lint: target → Guard C."""
    fake_root = tmp_path / "source"
    # No `^ci-lint:` target in the Makefile — checker should hit the
    # `[skip] $name: no make ci-lint target` branch and reach ran=0 with fail=0.
    _make_repo_with_feat_branch(tmp_path, "repo-a", REQ_ID, makefile="other:\n\t@true\n")
    r = _run(_patched_cmd(build_dev_cross_check_cmd, str(fake_root)))
    assert r.returncode != 0, (
        f"dev_cross_check Guard C MUST fire when feat present + no ci-lint target.\n"
        f"rc={r.returncode}\nstderr: {r.stderr}"
    )
    assert "0 source repos eligible" in r.stderr, (
        f"dev_cross_check Guard C MUST emit '0 source repos eligible'.\nstderr: {r.stderr}"
    )
    # And the new fail-loud message MUST NOT appear here — the repo *did* have
    # a feat branch, so this is a Makefile-target issue, not analyze-agent's.
    assert "has no feat/" not in r.stderr, (
        f"dev_cross_check: false-positive fail-loud emitted while feat branch was present.\n"
        f"stderr: {r.stderr}"
    )


def test_staging_test_guard_c_fires_when_feat_branch_present_but_missing_targets(tmp_path):
    """CNFB-S4 (≡ CESG-S9 done correctly): feat present + Makefile lacks both targets → Guard C."""
    fake_root = tmp_path / "source"
    # Has ci-unit-test but lacks ci-integration-test — repo skipped via the
    # `missing ci-unit-test or ci-integration-test target` branch.
    _make_repo_with_feat_branch(
        tmp_path, "repo-a", REQ_ID,
        makefile="ci-unit-test:\n\t@true\n",
    )
    r = _run(_patched_cmd(build_staging_test_cmd, str(fake_root)))
    assert r.returncode != 0
    assert "0 source repos eligible" in r.stderr, (
        f"staging_test Guard C MUST emit '0 source repos eligible'.\nstderr: {r.stderr}"
    )
    assert "has no feat/" not in r.stderr, (
        f"staging_test: false-positive fail-loud emitted while feat branch was present.\n"
        f"stderr: {r.stderr}"
    )


# ── CNFB-S5: spec_lint behavior unchanged ────────────────────────────────────


def test_spec_lint_no_feat_branch_still_silent_skip_to_guard_c(tmp_path):
    """CNFB-S5: spec_lint MUST keep its prior CESG-S3 behavior — Guard C, no fail-loud emission.

    Spec changes may legitimately consolidate in a single spec_home repo, so a
    cloned repo without feat/<REQ> is not necessarily a structural failure for
    spec_lint. The new fail-loud path applies only to dev_cross_check +
    staging_test.
    """
    fake_root = tmp_path / "source"
    _make_repo_without_feat_branch(tmp_path, "repo-a")
    r = _run(_patched_cmd(build_spec_lint_cmd, str(fake_root)))
    assert r.returncode != 0
    assert "0 source repos eligible" in r.stderr, (
        f"spec_lint: Guard C MUST still fire on missing feat branch.\nstderr: {r.stderr}"
    )
    assert "has no feat/" not in r.stderr, (
        f"spec_lint: this REQ MUST NOT introduce a per-repo fail-loud line.\n"
        f"stderr: {r.stderr}"
    )


# ── CNFB-S6: cmd template carries the new fail-loud literal (cheap grep) ─────


@pytest.mark.parametrize(
    "builder,name",
    [
        pytest.param(build_dev_cross_check_cmd, "dev_cross_check", id="dev_cross_check"),
        pytest.param(build_staging_test_cmd, "staging_test", id="staging_test"),
    ],
)
def test_build_cmd_emits_per_repo_no_feat_branch_fail_loud_literal(builder, name):
    """CNFB-S6: shell template MUST contain the per-repo fail-loud line + fail=1 marker."""
    cmd = builder(REQ_ID)
    assert f"FAIL {name}: $name has no feat/{REQ_ID} branch reachable on origin" in cmd, (
        f"{name}: shell template missing per-repo fail-loud literal.\n"
        f"got cmd: {cmd}"
    )
    assert "refusing to silent-pass" in cmd
    # The deprecated silent-skip line MUST NOT survive in the template.
    assert "no feat branch / not involved" not in cmd, (
        f"{name}: deprecated [skip] line still present in template."
    )
    # Guard C now must AND fail=0, otherwise the new fail-loud path would also
    # echo the misleading '0 source repos eligible' on top of the per-repo line.
    assert '"$ran" -eq 0' in cmd
    assert '[ "$fail" -eq 0 ]' in cmd or '[ "$fail" -eq 0 ];' in cmd
