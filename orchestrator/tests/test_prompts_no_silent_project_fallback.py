"""Regression: _shared/tools_whitelist.md.j2 must not silently fall back to a
hard-coded BKD project (workflowtest / illwkr1k).

Why: PR #67 traced a misrouted-stage incident to actions calling render() without
project_id/project_alias, which let the template's `or 'workflowtest'` fallback
silently route agents to the wrong BKD project. All 9 actions now pass both args
explicitly; this test guards that the fallback literals don't creep back into the
template.
"""
from __future__ import annotations

from orchestrator.prompts import render

_FORBIDDEN_FALLBACKS = ("workflowtest", "illwkr1k")


def _render_with(**ctx) -> str:
    # _shared/tools_whitelist.md.j2 is included from every stage prompt; render
    # it via a host that always references it (intake.md.j2 is a thin wrapper).
    return render("intake.md.j2", **ctx)


def test_template_uses_provided_project_alias_and_id():
    out = _render_with(
        project_alias="my-real-project",
        project_id="abc123def",
        issue_id="iss-xyz",
        intent_issue_id="iss-xyz",
        aissh_server_id="srv-1",
    )
    assert "PROJECT=my-real-project" in out
    assert "abc123def" in out
    for forbidden in _FORBIDDEN_FALLBACKS:
        assert forbidden not in out, (
            f"Hard-coded fallback {forbidden!r} re-appeared in tools_whitelist.md.j2"
        )


def test_missing_project_alias_does_not_silently_fall_back():
    """If a future caller forgets to pass project_alias, the rendered prompt MUST
    NOT silently substitute 'workflowtest' / 'illwkr1k'. Empty values are OK
    (the resulting `PROJECT=` makes the agent's first curl visibly fail, exactly
    the loud-fail behavior PR #67 wanted)."""
    out = _render_with(
        # deliberately omit project_alias / project_id
        issue_id="iss-xyz",
        intent_issue_id="iss-xyz",
        aissh_server_id="srv-1",
    )
    for forbidden in _FORBIDDEN_FALLBACKS:
        assert forbidden not in out, (
            f"_shared/tools_whitelist.md.j2 silently fell back to {forbidden!r} "
            "when project_alias/project_id were missing — drop the `or '...'` "
            "default so the failure is loud at first agent step instead of "
            "misrouting the stage to the wrong BKD project."
        )
