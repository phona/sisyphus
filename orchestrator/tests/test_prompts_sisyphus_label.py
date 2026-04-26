"""Regression: prompts MUST instruct agents to label sisyphus-opened PRs / BKD
sub-issues with `sisyphus`. See SAL-S5 / SAL-S6 in
openspec/changes/REQ-pr-label-sisyphus-auto-opened-1777217850/specs/sisyphus-auto-label/spec.md.
"""
from __future__ import annotations

from orchestrator.prompts import render


def _render_analyze() -> str:
    return render(
        "analyze.md.j2",
        req_id="REQ-x",
        project_id="proj-1",
        project_alias="proj-1",
        issue_id="iss-1",
        cloned_repos=["<owner>/repo-a"],
        aissh_server_id="srv-1",
    )


def test_analyze_prompt_requires_gh_pr_create_with_sisyphus_label() -> None:
    """SAL-S5: rendered analyze prompt MUST contain
    `gh label create sisyphus` (idempotent ensure-label) and `--label sisyphus`
    (so every PR carries the pipeline-identity label).
    """
    text = _render_analyze()
    assert "gh label create sisyphus" in text, (
        "analyze.md.j2 must instruct agents to idempotently create the "
        "`sisyphus` GitHub label before `gh pr create`. Without `gh label "
        "create --force` the first PR in a repo lacking the label fails "
        "with `pull request create: could not add label: 'sisyphus' not "
        "found` (SAL-S5)."
    )
    assert "--label sisyphus" in text, (
        "analyze.md.j2 must instruct agents to pass `--label sisyphus` to "
        "`gh pr create` so every sisyphus-opened PR is identifiable in "
        "GitHub UI / dashboards (SAL-S5)."
    )


def test_tools_whitelist_curl_post_example_includes_sisyphus_tag() -> None:
    """SAL-S6: the curl POST sub-issue example in tools_whitelist.md.j2
    (included from analyze.md.j2 via Jinja {% include %}) MUST show the
    `sisyphus` tag in its `tags` array, so any sub-agent that copies the
    example automatically inherits the label.
    """
    text = _render_analyze()
    # The curl POST example block:
    #   curl -sS -X POST http://localhost:3000/api/projects/$PROJECT/issues
    # ...then the `-d` JSON body must show "sisyphus" in tags.
    assert 'curl -sS -X POST http://localhost:3000/api/projects/$PROJECT/issues' in text, (
        "tools_whitelist.md.j2 must keep the canonical curl POST sub-issue "
        "example (SAL-S6 anchor)."
    )
    assert '"tags":["sisyphus"' in text or '"sisyphus", ' in text or '"sisyphus",' in text, (
        "tools_whitelist.md.j2's curl POST sub-issue example must include "
        "`\"sisyphus\"` in the `tags` array so copy-paste sub-agents inherit "
        "the pipeline-identity tag (SAL-S6)."
    )
