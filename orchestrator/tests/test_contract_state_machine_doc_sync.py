"""Contract tests: docs/state-machine.md sync with #118 #122 #124.
REQ-state-machine-doc-sync-1777189159

Black-box challenger. Derived from:
  openspec/changes/REQ-state-machine-doc-sync-1777189159/specs/state-machine-doc/spec.md

Scenarios covered:
  SMD-S1  archiving row: 不 auto-merge / 不 push main / #124 / NOT 合 PR
  SMD-S2  gh-incident-open row: #118 + #122 / correct function name open_incident / NOT file_incident
  SMD-S3  gh-incident-open row: per-repo loop / ctx.gh_incident_urls / all 5 fallback layers

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import pathlib
import re


def _load_state_machine_doc() -> str:
    repo_root = pathlib.Path(__file__).parent.parent.parent
    doc_path = repo_root / "docs" / "state-machine.md"
    return doc_path.read_text(encoding="utf-8")


def _find_table_row(content: str, first_col: str) -> str:
    """Return the full table row whose first column matches first_col (backtick-quoted)."""
    pattern = re.compile(
        r"^\|[^|]*`" + re.escape(first_col) + r"`[^|]*\|.*$",
        re.MULTILINE,
    )
    matches = pattern.findall(content)
    assert matches, f"Table row with first column `{first_col}` not found in docs/state-machine.md"
    # Return all matched rows joined so multi-line tables also work
    return "\n".join(matches)


# ─── SMD-S1: archiving row reflects no-auto-merge contract ──────────────────

def test_smd_s1_archiving_contains_no_auto_merge() -> None:
    """SMD-S1: archiving row MUST contain '不 auto-merge'."""
    row = _find_table_row(_load_state_machine_doc(), "archiving")
    assert "不 auto-merge" in row, (
        f"archiving row missing '不 auto-merge'. Row content:\n{row}"
    )


def test_smd_s1_archiving_contains_no_push_main() -> None:
    """SMD-S1: archiving row MUST contain '不 push main'."""
    row = _find_table_row(_load_state_machine_doc(), "archiving")
    assert "不 push main" in row, (
        f"archiving row missing '不 push main'. Row content:\n{row}"
    )


def test_smd_s1_archiving_references_pr124() -> None:
    """SMD-S1: archiving row MUST reference PR #124."""
    row = _find_table_row(_load_state_machine_doc(), "archiving")
    assert "#124" in row, (
        f"archiving row missing reference to PR #124. Row content:\n{row}"
    )


def test_smd_s1_archiving_not_contain_merge_pr() -> None:
    """SMD-S1: archiving row MUST NOT contain '合 PR' (implies auto-merge)."""
    row = _find_table_row(_load_state_machine_doc(), "archiving")
    assert "合 PR" not in row, (
        f"archiving row still contains '合 PR' which implies auto-merge — must be removed. Row content:\n{row}"
    )


# ─── SMD-S2: gh-incident-open row: correct PRs and function name ─────────────

def test_smd_s2_gh_incident_references_pr118() -> None:
    """SMD-S2: gh-incident-open row MUST reference PR #118."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "#118" in row, (
        f"gh-incident-open row missing reference to PR #118. Row content:\n{row}"
    )


def test_smd_s2_gh_incident_references_pr122() -> None:
    """SMD-S2: gh-incident-open row MUST reference PR #122."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "#122" in row, (
        f"gh-incident-open row missing reference to PR #122. Row content:\n{row}"
    )


def test_smd_s2_gh_incident_correct_function_name() -> None:
    """SMD-S2: gh-incident-open row MUST contain 'gh_incident.open_incident()'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "gh_incident.open_incident()" in row, (
        f"gh-incident-open row missing correct function name 'gh_incident.open_incident()'. Row content:\n{row}"
    )


def test_smd_s2_gh_incident_not_contain_file_incident() -> None:
    """SMD-S2: gh-incident-open row MUST NOT contain 'gh_incident.file_incident()'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "gh_incident.file_incident()" not in row, (
        f"gh-incident-open row still contains deprecated 'gh_incident.file_incident()'. Row content:\n{row}"
    )


# ─── SMD-S3: gh-incident-open row: per-repo loop + ctx shape + 5 fallbacks ───

def test_smd_s3_gh_incident_per_repo_loop() -> None:
    """SMD-S3: gh-incident-open row MUST contain '每个 involved source repo'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "每个 involved source repo" in row, (
        f"gh-incident-open row missing '每个 involved source repo'. Row content:\n{row}"
    )


def test_smd_s3_gh_incident_ctx_urls() -> None:
    """SMD-S3: gh-incident-open row MUST contain 'ctx.gh_incident_urls'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "ctx.gh_incident_urls" in row, (
        f"gh-incident-open row missing 'ctx.gh_incident_urls'. Row content:\n{row}"
    )


def test_smd_s3_gh_incident_fallback_intake_finalized_intent() -> None:
    """SMD-S3: gh-incident-open row MUST mention fallback layer 'intake_finalized_intent'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "intake_finalized_intent" in row, (
        f"gh-incident-open row missing fallback 'intake_finalized_intent'. Row content:\n{row}"
    )


def test_smd_s3_gh_incident_fallback_ctx_involved_repos() -> None:
    """SMD-S3: gh-incident-open row MUST mention fallback layer 'ctx.involved_repos'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "ctx.involved_repos" in row, (
        f"gh-incident-open row missing fallback 'ctx.involved_repos'. Row content:\n{row}"
    )


def test_smd_s3_gh_incident_fallback_repo_tag() -> None:
    """SMD-S3: gh-incident-open row MUST mention fallback layer 'repo:' tag."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "repo:" in row, (
        f"gh-incident-open row missing fallback 'repo:' tag. Row content:\n{row}"
    )


def test_smd_s3_gh_incident_fallback_default_involved_repos() -> None:
    """SMD-S3: gh-incident-open row MUST mention fallback layer 'default_involved_repos'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "default_involved_repos" in row, (
        f"gh-incident-open row missing fallback 'default_involved_repos'. Row content:\n{row}"
    )


def test_smd_s3_gh_incident_fallback_settings_gh_incident_repo() -> None:
    """SMD-S3: gh-incident-open row MUST mention fallback layer 'settings.gh_incident_repo'."""
    row = _find_table_row(_load_state_machine_doc(), "gh-incident-open")
    assert "settings.gh_incident_repo" in row, (
        f"gh-incident-open row missing fallback 'settings.gh_incident_repo'. Row content:\n{row}"
    )
