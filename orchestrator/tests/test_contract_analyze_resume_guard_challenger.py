"""Challenger contract tests for REQ-fix-analyze-resume-guard-1777431901.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-analyze-resume-guard-1777431901/specs/analyze-prompt/spec.md

Scenarios covered:
  ARG-S1  all checks negative allows normal execution
  ARG-S2  existing feature branch triggers guard
  ARG-S3  existing open PR triggers guard
  ARG-S4  existing openspec artifact triggers guard
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "prompts"
)

_ANALYZE_PATH = _PROMPTS_DIR / "analyze.md.j2"


def _read_analyze() -> str:
    return _ANALYZE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Structural: RESUME GUARD exists and is positioned before Part A
# ---------------------------------------------------------------------------


def test_arg_guard_section_exists_before_part_a() -> None:
    """ARG-S1 structural: analyze.md.j2 MUST contain a RESUME GUARD section
    placed before 'Part A'."""
    text = _read_analyze()
    guard_pos = text.find("## RESUME GUARD")
    part_a_pos = text.find("## Part A")
    assert guard_pos != -1, (
        "ARG-S1 FAILED: analyze.md.j2 does not contain a '## RESUME GUARD' section. "
        "The RESUME GUARD must be added at the top of the prompt before Part A."
    )
    assert part_a_pos != -1, (
        "ARG-S1 FAILED: analyze.md.j2 does not contain '## Part A'. "
        "This is a baseline structural requirement of the analyze prompt."
    )
    assert guard_pos < part_a_pos, (
        "ARG-S1 FAILED: '## RESUME GUARD' must appear BEFORE '## Part A'. "
        f"Found RESUME GUARD at position {guard_pos}, Part A at position {part_a_pos}."
    )


# ---------------------------------------------------------------------------
# ARG-S2 / ARG-S3 / ARG-S4 — the three objective checks are present
# ---------------------------------------------------------------------------


def test_arg_s2_branch_check_present() -> None:
    """ARG-S2: the prompt MUST instruct the agent to run
    `git ls-remote --heads origin feat/{REQ}` to verify the feature branch."""
    text = _read_analyze()
    assert "git ls-remote --heads origin feat/" in text, (
        "ARG-S2 FAILED: analyze.md.j2 does not contain the branch existence check. "
        "The prompt must instruct the agent to run "
        "`git ls-remote --heads origin feat/{REQ}` (or with template var `{{ req_id }}`)."
    )


def test_arg_s3_pr_check_present() -> None:
    """ARG-S3: the prompt MUST instruct the agent to run
    `gh pr list --head feat/{REQ} --state open` to verify an open PR."""
    text = _read_analyze()
    assert "gh pr list --head feat/" in text, (
        "ARG-S3 FAILED: analyze.md.j2 does not contain the PR existence check. "
        "The prompt must instruct the agent to run "
        "`gh pr list --head feat/{REQ} --state open` (or with template var)."
    )
    assert "--state open" in text, (
        "ARG-S3 FAILED: the PR check must use `--state open` to specifically "
        "look for open PRs (not closed or merged)."
    )


def test_arg_s4_openspec_artifact_check_present() -> None:
    """ARG-S4: the prompt MUST instruct the agent to run
    `git show origin/feat/{REQ}:openspec/changes/{REQ}/proposal.md`
    to verify the openspec artifact."""
    text = _read_analyze()
    assert "git show origin/feat/" in text, (
        "ARG-S4 FAILED: analyze.md.j2 does not contain the openspec artifact check. "
        "The prompt must instruct the agent to run "
        "`git show origin/feat/{REQ}:openspec/changes/{REQ}/proposal.md`."
    )
    assert "openspec/changes/" in text, (
        "ARG-S4 FAILED: the artifact check must reference the openspec changes path."
    )
    assert "proposal.md" in text, (
        "ARG-S4 FAILED: the artifact check must specifically look for proposal.md."
    )


# ---------------------------------------------------------------------------
# Guard trigger behavior — stop execution if ANY check succeeds
# ---------------------------------------------------------------------------


def test_arg_guard_trigger_message_present() -> None:
    """ARG-S2/S3/S4: the prompt MUST contain the guard-triggered message
    that the agent outputs when any check indicates work is already complete."""
    text = _read_analyze()
    assert "RESUME GUARD triggered" in text, (
        "ARG-S2/S3/S4 FAILED: analyze.md.j2 does not contain the guard-triggered message. "
        "When any check detects prior work, the agent must output a message containing "
        "'RESUME GUARD triggered'."
    )


def test_arg_guard_stop_instruction_present() -> None:
    """ARG-S2/S3/S4: the prompt MUST instruct the agent to stop execution
    immediately when any check is positive."""
    text = _read_analyze()
    # Look for stop/cease/halt instructions in Chinese or English
    stop_indicators = ["立即停止", "停止工作", "不再执行", "skipping", "skip"]
    has_stop = any(indicator in text.lower() for indicator in stop_indicators)
    assert has_stop, (
        "ARG-S2/S3/S4 FAILED: analyze.md.j2 does not contain a clear instruction "
        "to STOP execution when the guard is triggered. "
        "The prompt must tell the agent to cease all work."
    )


def test_arg_guard_any_check_logic_present() -> None:
    """ARG-S2/S3/S4 structural: the prompt must express that ANY of the three
    checks (not ALL) can trigger the guard."""
    text = _read_analyze()
    # Look for "任意" (any) or "任意一条" or "any" in the guard context
    any_indicators = ["任意", "any of", "any one", "任一", "任意一条"]
    has_any = any(indicator in text for indicator in any_indicators)
    assert has_any, (
        "ARG-S2/S3/S4 FAILED: analyze.md.j2 does not express the 'ANY check' logic. "
        "The guard must trigger if ANY (not ALL) of the three checks indicate "
        "prior work exists. Look for wording like '任意' or 'any of'."
    )


# ---------------------------------------------------------------------------
# Self-contained guard — must NOT query sisyphus internal state
# ---------------------------------------------------------------------------


def test_arg_guard_self_contained_no_sisyphus_state_queries() -> None:
    """ARG-S1/S2/S3/S4: the RESUME GUARD must be self-contained.
    It MUST NOT query sisyphus internal state (e.g. req_state, database,
    orchestrator API, BKD status beyond the basic checks)."""
    text = _read_analyze()
    # Find the guard section boundaries
    guard_start = text.find("## RESUME GUARD")
    part_a_start = text.find("## Part A")
    assert guard_start != -1 and part_a_start != -1, "Guard or Part A not found"
    guard_section = text[guard_start:part_a_start]

    forbidden_patterns = [
        "req_state",
        "postgres",
        "asyncpg",
        "database",
        "orchestrator/src",
        "state.py",
        "router.py",
        "store/",
    ]
    hits = []
    for pattern in forbidden_patterns:
        if pattern in guard_section.lower():
            hits.append(pattern)
    assert not hits, (
        "ARG-S1/S2/S3/S4 FAILED: RESUME GUARD section references sisyphus internal state. "
        "The guard MUST be self-contained and only use git/GitHub CLI commands. "
        f"Forbidden patterns found in guard section: {hits}"
    )
