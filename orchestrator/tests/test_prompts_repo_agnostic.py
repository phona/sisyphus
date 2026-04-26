"""Regression: orchestrator/src/orchestrator/prompts/ MUST NOT bake in
identifiers tied to the historical operator's GitHub org (`phona/`), the
historical product line (`ttpos-ci 标准` Makefile branding, `FEATURE-A`
acceptance scenario id prefix), or the orchestrator's own repo basename
(`/workspace/source/sisyphus`).

See openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271/specs/prompts-repo-agnostic/spec.md
for scenarios PRA-S1, PRA-S3, PRA-S5, PRA-S7 (the grep invariants enforced here).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "prompts"


def _all_prompt_files() -> list[Path]:
    return sorted(_PROMPTS_DIR.rglob("*.md.j2"))


def _matches(needle: str) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for f in _all_prompt_files():
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            if needle in line:
                hits.append((f.relative_to(_PROMPTS_DIR.parent.parent.parent), n, line.rstrip()))
    return hits


@pytest.mark.parametrize(
    "needle, why",
    [
        (
            "phona/",
            "GitHub org `phona/` is the historical operator; placeholder examples "
            "must use `<owner>/...` so sisyphus reads as repo-agnostic to new "
            "adopters. Match the convention already used by intake.md.j2's "
            "`involved_repos: [\"owner/repo-a\"]` example.",
        ),
        (
            "ttpos-ci 标准",
            "The Makefile target contract (ci-lint / ci-unit-test / "
            "ci-integration-test) is brand-neutral. Refer to it as 'Makefile ci "
            "契约' with a pointer to docs/integration-contracts.md, not the "
            "ttpos product-line brand.",
        ),
        (
            "FEATURE-A",
            "Acceptance scenario ids are spec-author-defined per repo. The "
            "accept / verifier-accept prompts must match `#### Scenario:` "
            "headings (the same pattern enforced by "
            "scripts/check-scenario-refs.sh), not a hardcoded `FEATURE-A` "
            "prefix.",
        ),
        (
            "/workspace/source/sisyphus",
            "Hardcoding the orchestrator's own basename only works for the M0 "
            "self-bootstrap REQ (sisyphus modifying sisyphus). Any other REQ "
            "would `cd: no such file`. Use /workspace/source/REPO instead.",
        ),
    ],
)
def test_prompt_directory_contains_no_repo_specific_bake_ins(needle: str, why: str) -> None:
    hits = _matches(needle)
    assert not hits, (
        f"Repo-specific bake-in {needle!r} re-appeared in shipped prompts.\n"
        f"Why this is forbidden: {why}\n"
        "Hits:\n  " + "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in hits)
    )


def test_decision_template_target_repo_example_uses_owner_placeholder() -> None:
    """PRA-S2 spot check: verifier/_decision.md.j2's `target_repo` example,
    which is the most-quoted spec-fragment in fixer feedback loops, must show
    the neutral `<owner>/...` form so fixer agents copy a portable example."""
    text = (_PROMPTS_DIR / "verifier" / "_decision.md.j2").read_text(encoding="utf-8")
    assert '"target_repo": "<owner>/repo-a"' in text, (
        "verifier/_decision.md.j2 lost the `<owner>/repo-a` example for "
        "`target_repo`. Restore it so multi-repo fixer prompts inherit a "
        "repo-agnostic template (PRA-S2)."
    )


def test_challenger_workspace_paths_use_basename_placeholder() -> None:
    """PRA-S8: challenger.md.j2 path segments under /workspace/source/ MUST
    refer to the basename form, because sisyphus-clone-repos.sh lays repos
    out at /workspace/source/<basename>/, NOT /workspace/source/<owner>/<repo>/.
    Using the `<owner>/<repo>` form here would `cd: no such file` at runtime."""
    text = (_PROMPTS_DIR / "challenger.md.j2").read_text(encoding="utf-8")
    assert "/workspace/source/<spec_home_repo>" not in text, (
        "challenger.md.j2 still uses `/workspace/source/<spec_home_repo>` as a "
        "path segment, but `<spec_home_repo>` placeholder holds `<owner>/<repo>` "
        "form (see done_archive.md.j2 example) — runner pod's clone helper lays "
        "repos out at /workspace/source/<basename>/. Use "
        "`<spec_home_repo_basename>` in path segments instead (PRA-S8)."
    )
    assert "<spec_home_repo_basename>" in text, (
        "challenger.md.j2 must reference `<spec_home_repo_basename>` in workspace "
        "paths and document that the basename is the GitHub repo name's last "
        "`/`-separated segment (PRA-S8)."
    )
