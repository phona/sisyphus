"""Contract tests for REQ-feat-precheck-373.

Scenarios covered:
  SPC-S1   _shared/precheck.md.j2 exists in the prompts package directory
  SPC-S2   precheck partial contains GH_TOKEN validity check
  SPC-S3   precheck partial contains KUBECONFIG check
  SPC-S4   precheck partial contains ci-precheck make target invocation
  SPC-S5   precheck partial contains result:fail tag instruction
  SPC-S6   precheck partial contains fail-reason:precheck: tag pattern
  SPC-S7   precheck partial documents skip semantics for missing target
  SPC-S8   analyze.md.j2 includes precheck partial
  SPC-S9   challenger.md.j2 includes precheck partial
  SPC-S10  accept.md.j2 includes precheck partial
  SPC-S11  staging_test.md.j2 includes precheck partial
  SPC-S12  bugfix.md.j2 includes precheck partial
  SPC-S13  pr_ci_watch.md.j2 does NOT include precheck partial (no make invocation)
  SPC-S14  intake.md.j2 does NOT include precheck partial (pure chat stage)
  SPC-S15  docs/integration-contracts.md contains ci-precheck section
  SPC-S16  integration-contracts ci-precheck section documents skip-if-missing semantics
  SPC-S17  precheck partial includes runner pod exec pattern (mcp ssh_exec)
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "prompts"
)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCS_DIR = _REPO_ROOT / "docs"


def _read_prompt(rel: str) -> str:
    return (_PROMPTS_DIR / rel).read_text(encoding="utf-8")


def _read_doc(rel: str) -> str:
    return (_DOCS_DIR / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SPC-S1  precheck partial exists
# ---------------------------------------------------------------------------


def test_spc_s1_precheck_partial_exists() -> None:
    """SPC-S1: _shared/precheck.md.j2 must exist in the prompts package directory."""
    path = _PROMPTS_DIR / "_shared" / "precheck.md.j2"
    assert path.exists(), (
        "SPC-S1 FAILED: _shared/precheck.md.j2 not found.\n"
        f"Expected path: {path}"
    )


# ---------------------------------------------------------------------------
# SPC-S2  GH_TOKEN check present
# ---------------------------------------------------------------------------


def test_spc_s2_precheck_contains_gh_token_check() -> None:
    """SPC-S2: precheck partial must instruct the agent to verify GH_TOKEN validity."""
    text = _read_prompt("_shared/precheck.md.j2")
    assert "GH_TOKEN" in text, (
        "SPC-S2 FAILED: _shared/precheck.md.j2 does not reference GH_TOKEN. "
        "The precheck must verify that GH_TOKEN is active before proceeding."
    )
    assert "api.github.com" in text, (
        "SPC-S2 FAILED: _shared/precheck.md.j2 does not contain a GitHub API probe. "
        "Expected a curl to api.github.com/user to validate GH_TOKEN."
    )


# ---------------------------------------------------------------------------
# SPC-S3  KUBECONFIG check present
# ---------------------------------------------------------------------------


def test_spc_s3_precheck_contains_kubeconfig_check() -> None:
    """SPC-S3: precheck partial must verify KUBECONFIG / kubectl cluster access."""
    text = _read_prompt("_shared/precheck.md.j2")
    assert "KUBECONFIG" in text or "kubectl cluster-info" in text, (
        "SPC-S3 FAILED: _shared/precheck.md.j2 does not contain a KUBECONFIG check. "
        "Expected `kubectl cluster-info` or a KUBECONFIG reference."
    )


# ---------------------------------------------------------------------------
# SPC-S4  ci-precheck make target invocation present
# ---------------------------------------------------------------------------


def test_spc_s4_precheck_invokes_ci_precheck_target() -> None:
    """SPC-S4: precheck partial must instruct the agent to run `make ci-precheck`."""
    text = _read_prompt("_shared/precheck.md.j2")
    assert "make ci-precheck" in text or "ci-precheck" in text, (
        "SPC-S4 FAILED: _shared/precheck.md.j2 does not mention `ci-precheck` make target. "
        "The precheck must attempt `make ci-precheck` on each source repo."
    )


# ---------------------------------------------------------------------------
# SPC-S5  result:fail tag instruction present
# ---------------------------------------------------------------------------


def test_spc_s5_precheck_emits_result_fail_tag() -> None:
    """SPC-S5: precheck partial must instruct the agent to tag the issue with result:fail on failure."""
    text = _read_prompt("_shared/precheck.md.j2")
    assert "result:fail" in text, (
        "SPC-S5 FAILED: _shared/precheck.md.j2 does not contain `result:fail` tag instruction. "
        "On any precheck failure the agent must PATCH the BKD issue with result:fail."
    )


# ---------------------------------------------------------------------------
# SPC-S6  fail-reason:precheck: tag pattern present
# ---------------------------------------------------------------------------


def test_spc_s6_precheck_emits_fail_reason_tag() -> None:
    """SPC-S6: precheck partial must include fail-reason:precheck: tag pattern."""
    text = _read_prompt("_shared/precheck.md.j2")
    assert "fail-reason:precheck:" in text, (
        "SPC-S6 FAILED: _shared/precheck.md.j2 does not include `fail-reason:precheck:` "
        "tag pattern. Each failure item must produce a distinct fail-reason tag "
        "(e.g. fail-reason:precheck:gh_token)."
    )


# ---------------------------------------------------------------------------
# SPC-S7  skip semantics documented for missing target
# ---------------------------------------------------------------------------


def test_spc_s7_precheck_documents_skip_when_target_missing() -> None:
    """SPC-S7: precheck partial must document that missing ci-precheck target means skip (not fail)."""
    text = _read_prompt("_shared/precheck.md.j2")
    has_skip_doc = "skip" in text.lower() or "不存在" in text or "No rule" in text
    assert has_skip_doc, (
        "SPC-S7 FAILED: _shared/precheck.md.j2 does not document skip semantics for "
        "a missing ci-precheck target. Old repos without the target must not be failed."
    )


# ---------------------------------------------------------------------------
# SPC-S8 – SPC-S12  stage prompts include precheck partial
# ---------------------------------------------------------------------------


_STAGES_WITH_PRECHECK = [
    "analyze.md.j2",
    "challenger.md.j2",
    "accept.md.j2",
    "staging_test.md.j2",
    "bugfix.md.j2",
]


@pytest.mark.parametrize("rel_path", _STAGES_WITH_PRECHECK)
def test_spc_s8_to_s12_stage_includes_precheck(rel_path: str) -> None:
    """SPC-S8 to SPC-S12: each runner-pod stage must include the precheck partial."""
    text = _read_prompt(rel_path)
    assert '_shared/precheck.md.j2' in text or "precheck.md.j2" in text, (
        f"SPC FAILED: {rel_path} does not include _shared/precheck.md.j2. "
        "All runner-pod stage agents must run the precheck phase as their first step."
    )


# ---------------------------------------------------------------------------
# SPC-S13  pr_ci_watch does NOT include precheck
# ---------------------------------------------------------------------------


def test_spc_s13_pr_ci_watch_excludes_precheck() -> None:
    """SPC-S13: pr_ci_watch.md.j2 must NOT include precheck (it only calls GitHub REST, no make)."""
    text = _read_prompt("pr_ci_watch.md.j2")
    assert "precheck.md.j2" not in text, (
        "SPC-S13 FAILED: pr_ci_watch.md.j2 includes precheck partial. "
        "pr-ci-watch only calls GitHub REST API and does not invoke make targets in "
        "source repos, so precheck is not applicable."
    )


# ---------------------------------------------------------------------------
# SPC-S14  intake does NOT include precheck
# ---------------------------------------------------------------------------


def test_spc_s14_intake_excludes_precheck() -> None:
    """SPC-S14: intake.md.j2 must NOT include precheck (pure chat stage, no runner work)."""
    text = _read_prompt("intake.md.j2")
    assert "precheck.md.j2" not in text, (
        "SPC-S14 FAILED: intake.md.j2 includes precheck partial. "
        "Intake is a pure chat stage with no runner pod invocations; precheck is not applicable."
    )


# ---------------------------------------------------------------------------
# SPC-S15  integration-contracts.md contains ci-precheck section
# ---------------------------------------------------------------------------


def test_spc_s15_integration_contracts_contains_ci_precheck_section() -> None:
    """SPC-S15: docs/integration-contracts.md must document the ci-precheck target contract."""
    text = _read_doc("integration-contracts.md")
    assert "ci-precheck" in text, (
        "SPC-S15 FAILED: docs/integration-contracts.md does not mention ci-precheck. "
        "The target contract must be documented so business repo owners know what to implement."
    )


# ---------------------------------------------------------------------------
# SPC-S16  integration-contracts ci-precheck section documents skip semantics
# ---------------------------------------------------------------------------


def test_spc_s16_integration_contracts_ci_precheck_documents_skip() -> None:
    """SPC-S16: the ci-precheck section in integration-contracts.md must document
    that a missing target means skip (backward-compatible with old repos)."""
    text = _read_doc("integration-contracts.md")
    # Find the relevant section around ci-precheck
    idx = text.find("ci-precheck")
    assert idx != -1, "ci-precheck not found in integration-contracts.md"
    # Check the surrounding ~2000 chars for skip semantics
    section = text[idx : idx + 2000]
    has_skip = "skip" in section.lower() or "不存在" in section or "No rule" in section
    assert has_skip, (
        "SPC-S16 FAILED: the ci-precheck section in integration-contracts.md does not "
        "document skip semantics when the target is missing. Old repos without the target "
        "must be silently skipped (not failed)."
    )


# ---------------------------------------------------------------------------
# SPC-S17  precheck partial uses mcp ssh_exec exec_run pattern
# ---------------------------------------------------------------------------


def test_spc_s17_precheck_uses_ssh_exec_pattern() -> None:
    """SPC-S17: precheck partial must use the mcp__ssh_exec__exec_run pattern
    to run commands on the runner pod (same pattern as runner_container.md.j2)."""
    text = _read_prompt("_shared/precheck.md.j2")
    assert "mcp__" in text and "exec_run" in text, (
        "SPC-S17 FAILED: _shared/precheck.md.j2 does not use the mcp__*__exec_run pattern. "
        "Commands must go through the ssh_exec MCP provider — BKD agents cannot run "
        "kubectl directly (no local kubectl / KUBECONFIG)."
    )
