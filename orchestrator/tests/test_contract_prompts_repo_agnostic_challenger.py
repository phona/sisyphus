"""Challenger contract tests for REQ-prompts-repo-agnostic-audit-1777189271.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271/specs/prompts-repo-agnostic/spec.md
  openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271/specs/prompts-repo-agnostic/contract.spec.yaml

Scenarios covered:
  PRA-S1  grep for `phona/` in prompts/ returns no matches
  PRA-S2  the 7 previously-phona/-containing files now use `<owner>/` or named placeholder
  PRA-S3  grep for `ttpos-ci 标准` in prompts/ returns no matches
  PRA-S4  every file that previously used `ttpos-ci 标准` now uses "Makefile ci 契约"
          AND at least one occurrence per file references docs/integration-contracts.md
  PRA-S5  grep for `FEATURE-A` in prompts/ returns no matches
  PRA-S6  accept.md.j2 Step 2 instruction cites `#### Scenario:` heading match,
          not a hardcoded `FEATURE-A` prefix, and uses a neutral scenario-id placeholder
  PRA-S7  grep for `/workspace/source/sisyphus` in prompts/ returns no matches
  PRA-S8  challenger.md.j2 workspace paths use `<spec_home_repo_basename>`, not
          `<spec_home_repo>` as a path segment, and include the one-line basename note
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "prompts"
)


def _read(rel: str) -> str:
    return (_PROMPTS_DIR / rel).read_text(encoding="utf-8")


def _grep(substring: str) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for f in sorted(_PROMPTS_DIR.rglob("*.md.j2")):
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            if substring in line:
                hits.append((f.relative_to(_PROMPTS_DIR.parent.parent.parent), lineno, line.rstrip()))
    return hits


# ---------------------------------------------------------------------------
# PRA-S1  no `phona/` anywhere in prompts/
# ---------------------------------------------------------------------------

def test_pra_s1_no_phona_org_in_prompts() -> None:
    """grep -RE 'phona/' orchestrator/src/orchestrator/prompts/ must produce no output."""
    hits = _grep("phona/")
    assert not hits, (
        "PRA-S1 FAILED: `phona/` (historical GitHub org) found in prompt templates.\n"
        "Replace with `<owner>/repo-a`, `<owner>/repo-b`, `<owner>/repo`, or a\n"
        "documented placeholder name (e.g. `<spec_home_repo>` without the org prefix).\n"
        "Hits:\n  " + "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in hits)
    )


# ---------------------------------------------------------------------------
# PRA-S2  the 7 previously-phona/-containing files no longer reference phona/
#         and use an owner-agnostic placeholder instead
# ---------------------------------------------------------------------------

_PRA_S2_FILES = [
    "analyze.md.j2",
    "done_archive.md.j2",
    "_shared/runner_container.md.j2",
    "challenger.md.j2",
    "verifier/dev_cross_check_fail.md.j2",
    "verifier/spec_lint_fail.md.j2",
    "verifier/_decision.md.j2",
]


@pytest.mark.parametrize("rel_path", _PRA_S2_FILES)
def test_pra_s2_previously_phona_files_contain_no_phona(rel_path: str) -> None:
    """PRA-S2: each of the 7 files that previously had `phona/` examples must now
    use `<owner>/...` or a documented placeholder name (e.g. `<spec_home_repo>`)."""
    text = _read(rel_path)
    assert "phona/" not in text, (
        f"PRA-S2 FAILED: {rel_path} still contains `phona/`. "
        "Replace with `<owner>/repo-a`, `<owner>/repo-b`, or the named placeholder "
        "`<spec_home_repo>` (for the sisyphus-clone-repos.sh `phona/<repo>` arg form)."
    )


def test_pra_s2_decision_template_uses_owner_placeholder() -> None:
    """PRA-S2 structural: verifier/_decision.md.j2 target_repo example uses
    `<owner>/repo-a` (the neutral form matching the spec's replacement_pattern)."""
    text = _read("verifier/_decision.md.j2")
    assert "<owner>/" in text, (
        "PRA-S2 FAILED: verifier/_decision.md.j2 must contain an `<owner>/` "
        "placeholder example (e.g. `\"target_repo\": \"<owner>/repo-a\"`) so "
        "fixer agents copy a repo-agnostic template."
    )


# ---------------------------------------------------------------------------
# PRA-S3  no `ttpos-ci 标准` anywhere in prompts/
# ---------------------------------------------------------------------------

def test_pra_s3_no_ttpos_ci_brand_in_prompts() -> None:
    """grep -RF 'ttpos-ci 标准' orchestrator/src/orchestrator/prompts/ must produce no output."""
    hits = _grep("ttpos-ci 标准")
    assert not hits, (
        "PRA-S3 FAILED: `ttpos-ci 标准` (product-line brand) found in prompt templates.\n"
        "The Makefile ci contract is brand-neutral; refer to it as 'Makefile ci 契约'\n"
        "with a pointer to docs/integration-contracts.md.\n"
        "Hits:\n  " + "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in hits)
    )


# ---------------------------------------------------------------------------
# PRA-S4  every file that previously read `ttpos-ci 标准` now:
#         (a) contains "Makefile ci 契约"
#         (b) references docs/integration-contracts.md at least once
# ---------------------------------------------------------------------------

_PRA_S4_FILES = [
    "analyze.md.j2",
    "bugfix.md.j2",
    "staging_test.md.j2",
    "_shared/runner_container.md.j2",
    "verifier/dev_cross_check_fail.md.j2",
    "verifier/dev_cross_check_success.md.j2",
]


@pytest.mark.parametrize("rel_path", _PRA_S4_FILES)
def test_pra_s4_replacement_uses_makefile_ci_contract_phrase(rel_path: str) -> None:
    """PRA-S4a: each file that replaced `ttpos-ci 标准` must now contain the literal
    phrase `Makefile ci 契约` (the canonical neutral wording per spec)."""
    text = _read(rel_path)
    assert "Makefile ci 契约" in text, (
        f"PRA-S4 FAILED: {rel_path} does not contain `Makefile ci 契约`.\n"
        "Every site that previously read 'ttpos-ci 标准' must be replaced with "
        "'Makefile ci 契约' (spec requirement PRA-S4)."
    )


@pytest.mark.parametrize("rel_path", _PRA_S4_FILES)
def test_pra_s4_replacement_links_to_integration_contracts_doc(rel_path: str) -> None:
    """PRA-S4b: at least one occurrence per file must reference
    `docs/integration-contracts.md` so a new agent can navigate to the canonical contract."""
    text = _read(rel_path)
    assert "docs/integration-contracts.md" in text, (
        f"PRA-S4 FAILED: {rel_path} does not reference `docs/integration-contracts.md`.\n"
        "At least one replacement per file must link to the canonical contract doc "
        "so new agents can navigate to the actual rules (spec requirement PRA-S4)."
    )


# ---------------------------------------------------------------------------
# PRA-S5  no `FEATURE-A` anywhere in prompts/
# ---------------------------------------------------------------------------

def test_pra_s5_no_feature_a_prefix_in_prompts() -> None:
    """grep -RF 'FEATURE-A' orchestrator/src/orchestrator/prompts/ must produce no output."""
    hits = _grep("FEATURE-A")
    assert not hits, (
        "PRA-S5 FAILED: `FEATURE-A` (hardcoded acceptance scenario id prefix) found "
        "in prompt templates.\n"
        "Acceptance scenario ids are spec-author-defined per repo. Use the heading\n"
        "match `#### Scenario:` (same as check-scenario-refs.sh) instead.\n"
        "Hits:\n  " + "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in hits)
    )


# ---------------------------------------------------------------------------
# PRA-S6  accept.md.j2 Step 2 uses `#### Scenario:` heading discriminator,
#         not a hardcoded prefix; reporting template example uses neutral placeholder
# ---------------------------------------------------------------------------

def test_pra_s6_accept_instructs_scenario_heading_match() -> None:
    """PRA-S6: accept.md.j2 must instruct the agent to find acceptance scenarios
    via `#### Scenario:` heading match (the same pattern as check-scenario-refs.sh),
    not via a hardcoded `FEATURE-A` prefix."""
    text = _read("accept.md.j2")
    assert "#### Scenario:" in text, (
        "PRA-S6 FAILED: accept.md.j2 does not reference `#### Scenario:` as the\n"
        "discriminator for finding acceptance scenario blocks. The instruction in\n"
        "Step 2 must direct the agent to enumerate every block whose heading matches\n"
        "`#### Scenario:` (neutral, per spec requirement PRA-S6)."
    )


def test_pra_s6_accept_reporting_example_uses_neutral_placeholder() -> None:
    """PRA-S6: the reporting template example in accept.md.j2 must NOT use
    `FEATURE-A1` / `FEATURE-A2` as scenario ids; it must use a neutral form
    such as `<scenario-id>` or a short id like `S1`."""
    text = _read("accept.md.j2")
    assert "FEATURE-A1" not in text, (
        "PRA-S6 FAILED: accept.md.j2 still uses `FEATURE-A1` in the reporting "
        "template example. Replace with a neutral placeholder like `<scenario-id>:1` "
        "or `S1: PASS` (spec requirement PRA-S6)."
    )
    assert "FEATURE-A2" not in text, (
        "PRA-S6 FAILED: accept.md.j2 still uses `FEATURE-A2` in the reporting "
        "template example. Replace with a neutral placeholder (spec requirement PRA-S6)."
    )


# ---------------------------------------------------------------------------
# PRA-S7  no `/workspace/source/sisyphus` anywhere in prompts/
# ---------------------------------------------------------------------------

def test_pra_s7_no_hardcoded_sisyphus_workspace_path_in_prompts() -> None:
    """grep -RF '/workspace/source/sisyphus' orchestrator/src/orchestrator/prompts/ must produce no output."""
    hits = _grep("/workspace/source/sisyphus")
    assert not hits, (
        "PRA-S7 FAILED: `/workspace/source/sisyphus` (hardcoded repo basename) found "
        "in prompt templates.\n"
        "Hardcoding the orchestrator's own basename only works for the M0 self-bootstrap\n"
        "REQ. Any other REQ would `cd: no such file`. Use `/workspace/source/REPO`\n"
        "(or a basename placeholder) instead.\n"
        "Hits:\n  " + "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in hits)
    )


# ---------------------------------------------------------------------------
# PRA-S8  challenger.md.j2 workspace paths use `<spec_home_repo_basename>`,
#         not `<spec_home_repo>` as a directory segment; includes basename note
# ---------------------------------------------------------------------------

def test_pra_s8_challenger_paths_do_not_use_owner_repo_form() -> None:
    """PRA-S8: challenger.md.j2 must NOT use `/workspace/source/<spec_home_repo>`
    because `<spec_home_repo>` is in `<owner>/<repo>` form, and sisyphus-clone-repos.sh
    clones to `/workspace/source/<basename>/` — not `<owner>/<repo>` nested path."""
    text = _read("challenger.md.j2")
    assert "/workspace/source/<spec_home_repo>" not in text, (
        "PRA-S8 FAILED: challenger.md.j2 uses `/workspace/source/<spec_home_repo>` "
        "as a directory path. `<spec_home_repo>` holds `<owner>/<repo>` form, but "
        "sisyphus-clone-repos.sh clones to `<basename>/`. Replace path segments with "
        "`<spec_home_repo_basename>` (spec requirement PRA-S8)."
    )


def test_pra_s8_challenger_paths_use_basename_placeholder() -> None:
    """PRA-S8: challenger.md.j2 must use `<spec_home_repo_basename>` in workspace
    path segments (the basename = last `/`-separated segment of `<owner>/<repo>`)."""
    text = _read("challenger.md.j2")
    assert "<spec_home_repo_basename>" in text, (
        "PRA-S8 FAILED: challenger.md.j2 must reference `<spec_home_repo_basename>` "
        "in `/workspace/source/...` paths and document that the basename is the "
        "GitHub repo name's last `/`-separated segment (spec requirement PRA-S8)."
    )


def test_pra_s8_challenger_includes_basename_explanation_note() -> None:
    """PRA-S8: challenger.md.j2 must include a note explaining that the basename
    is the GitHub repo name's last `/`-separated segment (per spec's 'one-line note'
    requirement), so agents know how to derive the basename at runtime."""
    text = _read("challenger.md.j2")
    has_note = (
        "basename" in text
        and (
            "last" in text
            or "最后" in text
            or "segment" in text
        )
    )
    assert has_note, (
        "PRA-S8 FAILED: challenger.md.j2 must include a note explaining that "
        "`<spec_home_repo_basename>` is the last `/`-separated segment of the "
        "GitHub repo name (e.g. the `repo` part of `owner/repo`). "
        "See spec requirement PRA-S8."
    )
