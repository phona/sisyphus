"""Challenger contract tests for REQ-ux-status-block-1777257283.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-ux-status-block-1777257283/specs/bkd-intent-status-block/spec.md
  openspec/changes/REQ-ux-status-block-1777257283/specs/bkd-intent-status-block/contract.spec.yaml

Scenarios covered:
  BISB-S1  partial renders 7-row table when every field is set
  BISB-S2  partial omits optional rows when inputs are unset
  BISB-S3  analyze prompt opens with status block above tools_whitelist
  BISB-S4  intake prompt opens with status block and omits cloned_repos
  BISB-S5  linked PRs row formats pr_urls via format_pr_links_inline
  BISB-S6  helper collapses empty inputs to None for clean row drop
  BISB-S7  omitted status_block kwarg is a no-op for backwards compat
"""
from __future__ import annotations

from orchestrator.prompts import render
from orchestrator.prompts.status_block import build_status_block_ctx


def _render_partial(status_block) -> str:
    return render("_shared/status_block.md.j2", status_block=status_block)


def _render_analyze(**extra) -> str:
    return render(
        "analyze.md.j2",
        req_id="REQ-foo",
        project_id="proj-1",
        project_alias="proj-1",
        issue_id="iss-1",
        cloned_repos=["<owner>/repo-a"],
        aissh_server_id="srv-1",
        **extra,
    )


def _render_intake(**extra) -> str:
    return render(
        "intake.md.j2",
        project_alias="proj-1",
        project_id="proj-1",
        issue_id="iss-1",
        intent_issue_id="iss-1",
        aissh_server_id="srv-1",
        **extra,
    )


# ---------------------------------------------------------------------------
# BISB-S1  partial renders 7-row table when every field is set
# ---------------------------------------------------------------------------

def test_bisb_s1_partial_renders_7row_table_all_fields_set() -> None:
    ctx = build_status_block_ctx(
        req_id="REQ-foo",
        stage="analyze",
        bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1",
        cloned_repos=["phona/sisyphus", "ZonEaseTech/ttpos-server-go"],
        pr_urls={"phona/sisyphus": "https://github.com/phona/sisyphus/pull/123"},
    )
    out = _render_partial(ctx)

    assert out.strip().startswith("## REQ Status"), (
        "BISB-S1: partial output must start with '## REQ Status'"
    )
    for field in ("REQ", "Stage", "Branch", "Runner Pod",
                  "BKD intent issue", "Pre-cloned repos", "Linked PRs"):
        assert field in out, f"BISB-S1: expected row '{field}' missing from partial output"

    assert "`REQ-foo`" in out, "BISB-S1: REQ row must contain `REQ-foo`"
    assert "`analyze`" in out, "BISB-S1: Stage row must contain `analyze`"
    assert "`feat/REQ-foo`" in out, "BISB-S1: Branch row must contain `feat/REQ-foo`"
    assert "`runner-req-foo`" in out, "BISB-S1: Runner Pod row must contain `runner-req-foo`"
    assert "https://bkd.example/projects/p/issues/iss-1" in out, (
        "BISB-S1: BKD intent issue row must contain the URL"
    )
    assert out.rstrip().endswith("---"), (
        "BISB-S1: partial output must end with trailing horizontal rule '---'"
    )


# ---------------------------------------------------------------------------
# BISB-S2  partial omits optional rows when inputs are unset
# ---------------------------------------------------------------------------

def test_bisb_s2_partial_omits_optional_rows_when_unset() -> None:
    ctx = build_status_block_ctx(
        req_id="REQ-bar",
        stage="intake",
        bkd_intent_issue_url=None,
        cloned_repos=None,
        pr_urls=None,
    )
    out = _render_partial(ctx)

    for field in ("REQ", "Stage", "Branch", "Runner Pod"):
        assert field in out, f"BISB-S2: always-on row '{field}' must be present"

    for field in ("BKD intent issue", "Pre-cloned repos", "Linked PRs"):
        assert field not in out, (
            f"BISB-S2: optional row '{field}' must not appear when input is None"
        )

    assert "| |" not in out and "|  |" not in out, (
        "BISB-S2: output must not contain empty markdown table cells"
    )


# ---------------------------------------------------------------------------
# BISB-S3  analyze prompt opens with status block above tools_whitelist
# ---------------------------------------------------------------------------

def test_bisb_s3_analyze_prompt_status_block_above_tools_whitelist() -> None:
    sb = build_status_block_ctx(
        req_id="REQ-foo",
        stage="analyze",
        bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1",
        cloned_repos=["phona/sisyphus"],
    )
    out = _render_analyze(status_block=sb)

    assert "## REQ Status" in out, (
        "BISB-S3: analyze prompt must contain '## REQ Status'"
    )
    idx_status = out.index("## REQ Status")
    assert "## 工具白名单" in out, (
        "BISB-S3: analyze prompt must contain '## 工具白名单'"
    )
    idx_whitelist = out.index("## 工具白名单")
    assert idx_status < idx_whitelist, (
        "BISB-S3: '## REQ Status' must appear before '## 工具白名单' in analyze prompt"
    )
    assert "Pre-cloned repos" in out, (
        "BISB-S3: analyze prompt must contain 'Pre-cloned repos' row with value"
    )


# ---------------------------------------------------------------------------
# BISB-S4  intake prompt opens with status block and omits cloned_repos
# ---------------------------------------------------------------------------

def test_bisb_s4_intake_prompt_status_block_omits_cloned_repos() -> None:
    sb = build_status_block_ctx(
        req_id="REQ-foo",
        stage="intake",
        bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1",
    )
    out = _render_intake(status_block=sb)

    assert "## REQ Status" in out, (
        "BISB-S4: intake prompt must contain '## REQ Status'"
    )
    assert "## 工具白名单" in out, (
        "BISB-S4: intake prompt must contain '## 工具白名单'"
    )
    idx_status = out.index("## REQ Status")
    idx_whitelist = out.index("## 工具白名单")
    assert idx_status < idx_whitelist, (
        "BISB-S4: '## REQ Status' must appear before '## 工具白名单' in intake prompt"
    )
    assert "Pre-cloned repos" not in out, (
        "BISB-S4: intake prompt must not contain 'Pre-cloned repos' (intake never pre-clones)"
    )
    assert "Linked PRs" not in out, (
        "BISB-S4: intake prompt must not contain 'Linked PRs' (no PRs at intake)"
    )
    assert "BKD intent issue" in out, (
        "BISB-S4: intake prompt must contain 'BKD intent issue' row"
    )
    assert "https://bkd.example/projects/p/issues/iss-1" in out, (
        "BISB-S4: BKD intent issue row must contain the supplied URL"
    )


# ---------------------------------------------------------------------------
# BISB-S5  linked PRs row formats pr_urls via format_pr_links_inline
# ---------------------------------------------------------------------------

def test_bisb_s5_pr_urls_formatted_as_inline_links() -> None:
    ctx = build_status_block_ctx(
        req_id="REQ-foo",
        stage="analyze",
        pr_urls={
            "phona/sisyphus": "https://github.com/phona/sisyphus/pull/42",
            "ZonEaseTech/ttpos-server-go": "https://github.com/ZonEaseTech/ttpos-server-go/pull/7",
        },
    )
    out = _render_partial(ctx)

    assert "[phona/sisyphus#42](https://github.com/phona/sisyphus/pull/42)" in out, (
        "BISB-S5: Linked PRs row must contain formatted link for phona/sisyphus#42"
    )
    assert (
        "[ZonEaseTech/ttpos-server-go#7](https://github.com/ZonEaseTech/ttpos-server-go/pull/7)"
        in out
    ), "BISB-S5: Linked PRs row must contain formatted link for ttpos-server-go#7"

    linked_prs_line = next(
        (line for line in out.splitlines() if "Linked PRs" in line), None
    )
    assert linked_prs_line is not None, "BISB-S5: Linked PRs row must exist in output"
    assert "," in linked_prs_line, (
        "BISB-S5: two PR links in the Linked PRs row must be comma-separated"
    )


# ---------------------------------------------------------------------------
# BISB-S6  helper collapses empty inputs to None for clean row drop
# ---------------------------------------------------------------------------

def test_bisb_s6_helper_collapses_empty_inputs_to_none() -> None:
    ctx = build_status_block_ctx(
        req_id="REQ-foo",
        stage="intake",
        bkd_intent_issue_url="",
        cloned_repos=[],
        pr_urls={},
    )

    def _get(obj, key):
        return obj[key] if isinstance(obj, dict) else getattr(obj, key)

    assert _get(ctx, "bkd_intent_issue_url") is None, (
        "BISB-S6: empty string bkd_intent_issue_url must collapse to None"
    )
    assert _get(ctx, "cloned_repos") is None, (
        "BISB-S6: empty list cloned_repos must collapse to None"
    )
    assert _get(ctx, "pr_links_inline") is None, (
        "BISB-S6: empty dict pr_urls must collapse to None pr_links_inline"
    )

    req_id_val = _get(ctx, "req_id")
    stage_val = _get(ctx, "stage")
    assert req_id_val == "REQ-foo", "BISB-S6: req_id must be preserved verbatim"
    assert stage_val == "intake", "BISB-S6: stage must be preserved verbatim"

    out = _render_partial(ctx)
    for field in ("BKD intent issue", "Pre-cloned repos", "Linked PRs"):
        assert field not in out, (
            f"BISB-S6: optional row '{field}' must not appear after empty→None collapse"
        )


# ---------------------------------------------------------------------------
# BISB-S7  omitted status_block kwarg is a no-op for backwards compat
# ---------------------------------------------------------------------------

def test_bisb_s7_omitted_status_block_is_noop() -> None:
    out_with_none = _render_analyze(status_block=None)
    out_without = _render_analyze()

    assert out_with_none.strip() == out_without.strip(), (
        "BISB-S7: rendering with status_block=None and without status_block kwarg "
        "must produce byte-identical output (after strip)"
    )
    assert "## REQ Status" not in out_without, (
        "BISB-S7: when status_block is not passed, '## REQ Status' must not appear"
    )
