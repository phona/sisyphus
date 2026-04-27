"""REQ-ux-status-block-1777257283: BKD intent body status block tests.

Covers spec scenarios BISB-S1..S7 in
``openspec/changes/REQ-ux-status-block-1777257283/specs/bkd-intent-status-block/spec.md``.
The partial under test renders a markdown ``## REQ Status`` table that gets
included at the top of ``intake.md.j2`` and ``analyze.md.j2``.
"""
from __future__ import annotations

from orchestrator.prompts import render
from orchestrator.prompts.status_block import build_status_block_ctx


def _render_partial(status_block: dict | None) -> str:
    return render("_shared/status_block.md.j2", status_block=status_block)


def _data_row_field_names(rendered: str) -> list[str]:
    """Pick out the ``Field`` column of each data row in the rendered table."""
    rows: list[str] = []
    for line in rendered.splitlines():
        if not line.startswith("| "):
            continue
        if line.startswith("| Field"):
            continue  # header row
        if line.startswith("|---"):
            continue  # markdown separator
        rows.append(line)
    return [r.split(" | ", 1)[0].lstrip("| ") for r in rows]


# ── BISB-S1 ────────────────────────────────────────────────────────────────


def test_bisb_s1_partial_renders_seven_rows_when_every_field_set() -> None:
    """BISB-S1: full table, 7 data rows in documented order."""
    sb = {
        "req_id": "REQ-foo",
        "stage": "analyze",
        "bkd_intent_issue_url": "https://bkd.example/projects/p/issues/iss-1",
        "cloned_repos": ["phona/sisyphus", "ZonEaseTech/ttpos-server-go"],
        "pr_links_inline":
            "[phona/sisyphus#123](https://github.com/phona/sisyphus/pull/123)",
    }
    out = _render_partial(sb)
    assert out.lstrip().startswith("## REQ Status"), out[:200]
    assert _data_row_field_names(out) == [
        "REQ", "Stage", "Branch", "Runner Pod",
        "BKD intent issue", "Pre-cloned repos", "Linked PRs",
    ]
    assert "`REQ-foo`" in out
    assert "`analyze`" in out
    assert "`feat/REQ-foo`" in out
    assert "`runner-req-foo`" in out
    assert "[open](https://bkd.example/projects/p/issues/iss-1)" in out
    assert "phona/sisyphus, ZonEaseTech/ttpos-server-go" in out
    assert ("[phona/sisyphus#123]"
            "(https://github.com/phona/sisyphus/pull/123)") in out
    assert out.rstrip().endswith("---"), out[-100:]


# ── BISB-S2 ────────────────────────────────────────────────────────────────


def test_bisb_s2_partial_omits_optional_rows_when_unset() -> None:
    """BISB-S2: only req_id + stage → 4 always-on rows, no optional row strings."""
    sb = {
        "req_id": "REQ-bar",
        "stage": "intake",
        "bkd_intent_issue_url": None,
        "cloned_repos": None,
        "pr_links_inline": None,
    }
    out = _render_partial(sb)
    assert _data_row_field_names(out) == ["REQ", "Stage", "Branch", "Runner Pod"]
    assert "BKD intent issue" not in out
    assert "Pre-cloned repos" not in out
    assert "Linked PRs" not in out
    # No empty markdown table cells.
    assert "| |" not in out
    assert "|  |" not in out


# ── BISB-S3 ────────────────────────────────────────────────────────────────


def test_bisb_s3_analyze_prompt_status_block_above_tools_whitelist() -> None:
    """BISB-S3: rendered analyze prompt has ## REQ Status before ## 工具白名单."""
    sb = build_status_block_ctx(
        req_id="REQ-foo",
        stage="analyze",
        bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1",
        cloned_repos=["phona/sisyphus"],
    )
    out = render(
        "analyze.md.j2",
        req_id="REQ-foo",
        project_id="proj-1",
        project_alias="proj-1",
        issue_id="iss-1",
        cloned_repos=["phona/sisyphus"],
        bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1",
        aissh_server_id="srv-1",
        status_block=sb,
    )
    idx_status = out.find("## REQ Status")
    idx_tools = out.find("## 工具白名单")
    assert idx_status >= 0, "## REQ Status missing from rendered analyze prompt"
    assert idx_tools > idx_status, (idx_status, idx_tools)
    assert "Pre-cloned repos" in out
    assert "phona/sisyphus" in out


# ── BISB-S4 ────────────────────────────────────────────────────────────────


def test_bisb_s4_intake_prompt_status_block_omits_cloned_repos_and_prs() -> None:
    """BISB-S4: intake → status block has BKD intent URL but no cloned_repos/PRs."""
    sb = build_status_block_ctx(
        req_id="REQ-foo",
        stage="intake",
        bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1",
    )
    out = render(
        "intake.md.j2",
        req_id="REQ-foo",
        project_id="proj-1",
        project_alias="proj-1",
        issue_id="iss-1",
        aissh_server_id="srv-1",
        bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1",
        status_block=sb,
    )
    idx_status = out.find("## REQ Status")
    idx_tools = out.find("## 工具白名单")
    assert idx_status >= 0, "## REQ Status missing from rendered intake prompt"
    assert idx_tools > idx_status, (idx_status, idx_tools)
    # Pre-cloned repos / Linked PRs strings only live in the partial; their
    # absence proves those rows were omitted from the intake render.
    assert "Pre-cloned repos" not in out
    assert "Linked PRs" not in out
    assert "BKD intent issue" in out
    assert "https://bkd.example/projects/p/issues/iss-1" in out


# ── BISB-S5 ────────────────────────────────────────────────────────────────


def test_bisb_s5_pr_urls_renders_clickable_inline_links() -> None:
    """BISB-S5: pr_urls dict → clickable per-repo markdown links, comma-joined."""
    sb = build_status_block_ctx(
        req_id="REQ-foo",
        stage="analyze",
        pr_urls={
            "phona/sisyphus": "https://github.com/phona/sisyphus/pull/42",
            "ZonEaseTech/ttpos-server-go":
                "https://github.com/ZonEaseTech/ttpos-server-go/pull/7",
        },
    )
    out = _render_partial(sb)
    pr_rows = [line for line in out.splitlines() if "Linked PRs" in line]
    assert len(pr_rows) == 1, pr_rows
    pr_row = pr_rows[0]
    assert ("[phona/sisyphus#42]"
            "(https://github.com/phona/sisyphus/pull/42)") in pr_row
    assert ("[ZonEaseTech/ttpos-server-go#7]"
            "(https://github.com/ZonEaseTech/ttpos-server-go/pull/7)") in pr_row
    assert ", " in pr_row  # the two links share one cell


# ── BISB-S6 ────────────────────────────────────────────────────────────────


def test_bisb_s6_helper_collapses_empty_inputs_to_none() -> None:
    """BISB-S6: empty/blank optional inputs → None so the partial drops the row."""
    sb = build_status_block_ctx(
        req_id="REQ-foo",
        stage="intake",
        bkd_intent_issue_url="",
        cloned_repos=[],
        pr_urls={},
    )
    assert sb["bkd_intent_issue_url"] is None
    assert sb["cloned_repos"] is None
    assert sb["pr_links_inline"] is None
    assert sb["req_id"] == "REQ-foo"
    assert sb["stage"] == "intake"
    out = _render_partial(sb)
    assert _data_row_field_names(out) == ["REQ", "Stage", "Branch", "Runner Pod"]


# ── BISB-S7 ────────────────────────────────────────────────────────────────


def test_bisb_s7_omitted_status_block_kwarg_is_noop_for_backwards_compat() -> None:
    """BISB-S7: status_block omitted vs status_block=None → byte-identical output."""
    base = {
        "req_id": "REQ-foo",
        "project_id": "proj-1",
        "project_alias": "proj-1",
        "issue_id": "iss-1",
        "cloned_repos": ["phona/sisyphus"],
        "aissh_server_id": "srv-1",
        "bkd_intent_issue_url": "https://bkd.example/projects/p/issues/iss-1",
    }
    out_omitted = render("analyze.md.j2", **base)
    out_none = render("analyze.md.j2", status_block=None, **base)
    assert out_omitted.strip() == out_none.strip()
    assert "## REQ Status" not in out_omitted
    assert "## REQ Status" not in out_none
