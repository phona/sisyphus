"""Challenger contract tests for REQ-feat-precheck-373-1777864856.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-feat-precheck-373-1777864856/specs/feat-stage-precheck/spec.md
  openspec/changes/REQ-feat-precheck-373-1777864856/proposal.md

Scenarios covered:
  PRECHECK-S1  hook renders ## Stage Precheck section + 3 check classes for the
               6 ssh-pod-bound stages (analyze / challenger / accept /
               staging_test / pr_ci_watch / bugfix)
  PRECHECK-S2  hook stays silent for chat-only stages (intake)
  PRECHECK-S3  hook stays silent when removed from enabled_prompt_hooks; sibling
               hook sections (mcp_preflight, self_issue_constraint) remain
  PRECHECK-S4  hook documents canonical fail tag scheme
               (`result:fail` + `fail-reason:precheck:`)
  PRECHECK-S5  hook references mcp__<provider>__exec_run via
               mcp_capability_providers indirection (no hard-coded `aissh-tao`)
  PRECHECK-S6  default enabled_prompt_hooks order is exactly
               ["mcp_preflight", "precheck", "self_issue_constraint"]

Dev MUST NOT modify these tests to make them pass — fix the implementation
instead. If a test is wrong, escalate to spec_fixer to correct the spec, not the
test.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

# 6 stage prompts whose agents work inside the runner pod (per spec Requirement
# body: "analyze / challenger / accept / staging_test / pr_ci_watch / bugfix").
_SSH_POD_STAGES: list[tuple[str, str]] = [
    ("execute", "execute.md.j2"),
    ("challenger", "challenger.md.j2"),
    ("accept", "accept.md.j2"),
    ("staging_test", "staging_test.md.j2"),
    ("pr_ci_watch", "pr_ci_watch.md.j2"),
    ("bugfix", "bugfix.md.j2"),
]

# Generous superset context — individual prompts only consume the subset they
# care about; surplus keys are ignored. We deliberately do NOT introspect each
# template's required keys (would couple the contract to dev's internal var
# naming). Default jinja `Undefined` makes `{{ missing }}` render as empty
# string, which is enough to assert section-level presence/absence.
_KITCHEN_SINK_CTX: dict[str, Any] = {
    "req_id": "REQ-feat-precheck-373-1777864856",
    "stage": "execute",
    "trigger": "success",
    "aissh_server_id": "test-server-id",
    "project_id": "nnvxh8wj",
    "project_alias": "nnvxh8wj",
    "issue_id": "iss-self",
    "intent_issue_id": "iss-intent",
    "parent_issue_id": "iss-parent",
    "src_issue_id": "iss-src",
    "accept_issue_id": "iss-acc",
    "cloned_repos": ["phona/sisyphus"],
    "bkd_intent_issue_url": "http://example.test/projects/nnvxh8wj/issues/intent",
    "status_block": {"stage": "execute", "req_id": "REQ-X"},
    "branch": "feat/REQ-feat-precheck-373-1777864856",
    "pr_url": "http://example.test/pr/1",
    "pr_number": 1,
    "repo_full_name": "phona/sisyphus",
    "spec_home_repo": "phona/sisyphus",
    "spec_home_repo_basename": "sisyphus",
    "history": [],
    "stderr_tail": "",
    "artifact_paths": [],
    "checker_stdout": "",
    "checker_stderr": "",
    "checker_exit_code": 0,
    "intake_summary": None,
}


def _prod_render(template_name: str, **extra_ctx: Any) -> str:
    """Render via the production renderer (uses real settings + globals)."""
    from orchestrator.prompts import render

    ctx = dict(_KITCHEN_SINK_CTX)
    ctx.update(extra_ctx)
    return render(template_name, **ctx)


def _isolated_env_render(
    template_name: str,
    *,
    enabled_prompt_hooks: list[str],
    mcp_capability_providers: dict[str, str] | None = None,
    stage_precheck_enabled: dict[str, bool] | None = None,
    extra_ctx: dict[str, Any] | None = None,
) -> str:
    """Render with a fresh jinja Environment whose globals we fully control.

    Used by override scenarios (S3, S5) so tests are not coupled to test order
    or to a monkey-patch on the production `_env` global.
    """
    from orchestrator.config import settings

    prompts_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "orchestrator"
        / "prompts"
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(prompts_dir)),
        autoescape=jinja2.select_autoescape(
            disabled_extensions=("md", "j2", "txt"), default=False,
        ),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["enabled_prompt_hooks"] = enabled_prompt_hooks
    env.globals["mcp_capability_providers"] = (
        mcp_capability_providers
        if mcp_capability_providers is not None
        else dict(settings.mcp_capability_providers)
    )
    env.globals["mcp_capability_probe_tools"] = dict(
        settings.mcp_capability_probe_tools,
    )
    env.globals["stage_mcp_requirements"] = dict(
        settings.stage_mcp_requirements,
    )
    env.globals["stage_precheck_enabled"] = (
        stage_precheck_enabled
        if stage_precheck_enabled is not None
        else dict(settings.stage_precheck_enabled)
    )

    ctx = dict(_KITCHEN_SINK_CTX)
    if extra_ctx:
        ctx.update(extra_ctx)
    return env.get_template(template_name).render(**ctx)


# ─────────────────────────────────────────────────────────────────────────────
# PRECHECK-S1  hook renders the section for every ssh-pod-bound stage
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("stage_name", "template"), _SSH_POD_STAGES)
def test_precheck_s1_section_emitted_for_ssh_pod_stages(
    stage_name: str, template: str,
) -> None:
    """PRECHECK-S1: with default config, every ssh-pod-bound stage prompt MUST
    contain the `## Stage Precheck` heading and reference all three precheck
    classes (env / tool / `make ci-precheck`)."""
    out = _prod_render(template, stage=stage_name)

    assert "## Stage Precheck" in out, (
        f"PRECHECK-S1 FAILED: {template} does not render the `## Stage Precheck`"
        f" heading under default config. The precheck hook must be included for"
        f" stage {stage_name!r} (one of analyze / challenger / accept /"
        f" staging_test / pr_ci_watch / bugfix)."
    )

    # Reference the three precheck classes per spec Requirement body.
    assert "SISYPHUS_REQ_ID" in out, (
        f"PRECHECK-S1 FAILED: {template} precheck section omits the env-var"
        f" check for SISYPHUS_REQ_ID (spec class 1: pod env)."
    )
    assert "GH_TOKEN" in out, (
        f"PRECHECK-S1 FAILED: {template} precheck section omits the env-var"
        f" check for GH_TOKEN (spec class 1: pod env)."
    )
    assert "KUBECONFIG" in out, (
        f"PRECHECK-S1 FAILED: {template} precheck section omits the env-var"
        f" check for KUBECONFIG (spec class 1: pod env)."
    )
    assert "gh auth status" in out, (
        f"PRECHECK-S1 FAILED: {template} precheck section omits the"
        f" `gh auth status` tool-presence smoke (spec class 2: tools)."
    )
    assert "kubectl" in out, (
        f"PRECHECK-S1 FAILED: {template} precheck section omits the kubectl"
        f" tool-presence smoke (spec class 2: tools)."
    )
    assert "make --version" in out or "make -n" in out or "make " in out, (
        f"PRECHECK-S1 FAILED: {template} precheck section omits any reference"
        f" to a `make` tool-presence / probe (spec class 2: tools)."
    )
    assert "ci-precheck" in out, (
        f"PRECHECK-S1 FAILED: {template} precheck section omits the per-repo"
        f" `make ci-precheck` invocation (spec class 3: business-repo opt-in)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRECHECK-S2  hook stays silent for chat-only stages (intake)
# ─────────────────────────────────────────────────────────────────────────────

def test_precheck_s2_intake_does_not_render_section() -> None:
    """PRECHECK-S2: under the default `stage_precheck_enabled` configuration,
    the intake.md.j2 prompt MUST NOT contain the substring `Stage Precheck`,
    so the chat-brainstorm flavour of intake is preserved."""
    out = _prod_render("intake.md.j2", stage="intake")

    assert "Stage Precheck" not in out, (
        "PRECHECK-S2 FAILED: intake.md.j2 rendered the precheck section.\n"
        "intake is a chat-only brainstorm stage; the default value of\n"
        "stage_precheck_enabled['intake'] MUST be False so the precheck hook\n"
        "stays silent (rendering nothing) for this template.\n"
        "If the section is appearing, either the default config flipped or\n"
        "the hook body is rendering unconditionally."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRECHECK-S3  hook stays silent when removed from enabled_prompt_hooks
#              while sibling hook sections remain (pluggable invariant)
# ─────────────────────────────────────────────────────────────────────────────

def test_precheck_s3_disabled_via_enabled_prompt_hooks() -> None:
    """PRECHECK-S3: if an operator removes `precheck` from
    `enabled_prompt_hooks`, the execute.md.j2 prompt MUST NOT contain the
    `Stage Precheck` substring; the other two sibling hook sections
    (`MCP 依赖预检` from mcp_preflight, `只改本 issue` from
    self_issue_constraint) MUST still be present — proving the for-loop honours
    `enabled_prompt_hooks` (pluggable hook invariant from REQ #270)."""
    out = _isolated_env_render(
        "execute.md.j2",
        enabled_prompt_hooks=["mcp_preflight", "self_issue_constraint"],
    )

    assert "Stage Precheck" not in out, (
        "PRECHECK-S3 FAILED: execute.md.j2 rendered the precheck section even\n"
        "though `precheck` was removed from `enabled_prompt_hooks`. The hook\n"
        "loop must gate inclusion strictly on the configured list — no\n"
        "unconditional `{% include %}` of the precheck partial may exist."
    )

    assert "MCP 依赖预检" in out, (
        "PRECHECK-S3 FAILED: execute.md.j2 stopped rendering the `MCP 依赖预检`\n"
        "section when only precheck was removed. The mcp_preflight hook must\n"
        "still render — confirming the for-loop is selective per hook name,\n"
        "not all-or-nothing."
    )

    assert "只改本 issue" in out, (
        "PRECHECK-S3 FAILED: execute.md.j2 stopped rendering the\n"
        "`只改本 issue` section when only precheck was removed. The\n"
        "self_issue_constraint hook must still render — confirming the\n"
        "for-loop is selective per hook name, not all-or-nothing."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRECHECK-S4  hook documents the canonical fail tag scheme
# ─────────────────────────────────────────────────────────────────────────────

def test_precheck_s4_documents_fail_tag_scheme() -> None:
    """PRECHECK-S4: when the precheck section is emitted (default config), the
    rendered execute.md.j2 text MUST contain the canonical fail-tag literals
    `result:fail` and `fail-reason:precheck:` so the agent emits the correct
    tags on hard fail (verifier escalates without retry)."""
    out = _prod_render("execute.md.j2", stage="execute")

    assert "result:fail" in out, (
        "PRECHECK-S4 FAILED: execute.md.j2 precheck section does not document\n"
        "the literal tag `result:fail`. Agents need to see this exact tag\n"
        "string to write the canonical fail tag on hard precheck failure."
    )

    assert "fail-reason:precheck:" in out, (
        "PRECHECK-S4 FAILED: execute.md.j2 precheck section does not document\n"
        "the literal tag prefix `fail-reason:precheck:`. Agents need this\n"
        "exact prefix to encode the failing item (e.g.\n"
        "`fail-reason:precheck:env:GH_TOKEN`,\n"
        "`fail-reason:precheck:tool:kubectl`,\n"
        "`fail-reason:precheck:ci-precheck:<repo>`)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRECHECK-S5  hook honours mcp_capability_providers['ssh_exec'] indirection
# ─────────────────────────────────────────────────────────────────────────────

def test_precheck_s5_uses_provider_indirection_not_hardcoded_aissh_tao() -> None:
    """PRECHECK-S5: when an operator overrides
    `mcp_capability_providers['ssh_exec']` to a different provider name, the
    precheck section in the rendered prompt MUST reference
    `mcp__<new-provider>__exec_run` and MUST NOT contain
    `mcp__aissh-tao__exec_run` — proving the hook does not hard-code the
    aissh-tao literal."""
    out = _isolated_env_render(
        "execute.md.j2",
        enabled_prompt_hooks=["mcp_preflight", "precheck", "self_issue_constraint"],
        mcp_capability_providers={"ssh_exec": "fake-ssh-provider"},
    )

    # Sanity: the precheck section MUST be present in this render path.
    assert "## Stage Precheck" in out, (
        "PRECHECK-S5 SETUP FAILED: precheck section was not rendered at all,\n"
        "so the provider-indirection assertion below is vacuous. Check that\n"
        "the test's enabled_prompt_hooks + stage_precheck_enabled defaults\n"
        "actually emit the section."
    )

    # Slice the precheck section so we don't catch unrelated mentions of
    # `mcp__aissh-tao__exec_run` in other hook sections (mcp_preflight, etc.).
    # The section starts at `## Stage Precheck` and ends at the next H2.
    start = out.index("## Stage Precheck")
    rest = out[start + len("## Stage Precheck"):]
    next_h2 = rest.find("\n## ")
    section = (
        rest[: next_h2]
        if next_h2 != -1
        else rest
    )

    assert "mcp__fake-ssh-provider__exec_run" in section, (
        "PRECHECK-S5 FAILED: precheck section does not reference\n"
        "`mcp__fake-ssh-provider__exec_run` after overriding\n"
        "mcp_capability_providers['ssh_exec'] = 'fake-ssh-provider'. The\n"
        "hook must compose the SSH-exec tool name from\n"
        "`mcp__{{ mcp_capability_providers['ssh_exec'] }}__exec_run`, not a\n"
        "hard-coded `aissh-tao` literal."
    )

    assert "mcp__aissh-tao__exec_run" not in section, (
        "PRECHECK-S5 FAILED: precheck section still contains the literal\n"
        "`mcp__aissh-tao__exec_run` after overriding the ssh_exec provider.\n"
        "The hook is hard-coding the default provider name; replace with\n"
        "`mcp__{{ mcp_capability_providers['ssh_exec'] }}__exec_run` so helm\n"
        "values overrides propagate."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PRECHECK-S6  default enabled_prompt_hooks ordering
# ─────────────────────────────────────────────────────────────────────────────

def test_precheck_s6_default_enabled_prompt_hooks_ordering() -> None:
    """PRECHECK-S6: the default `enabled_prompt_hooks` shipped by the
    orchestrator MUST equal exactly
    `["mcp_preflight", "precheck", "self_issue_constraint"]` (order matters:
    MCP must work before precheck can shell into the pod, and both fail-fast
    segments must precede the self-issue policy hook)."""
    from orchestrator.config import settings

    assert list(settings.enabled_prompt_hooks) == [
        "mcp_preflight",
        "precheck",
        "self_issue_constraint",
    ], (
        "PRECHECK-S6 FAILED: settings.enabled_prompt_hooks default is\n"
        f"  {list(settings.enabled_prompt_hooks)!r}\n"
        "but the spec requires exactly\n"
        "  ['mcp_preflight', 'precheck', 'self_issue_constraint']\n"
        "Order matters — mcp_preflight first (MCP must be up before\n"
        "precheck can shell), precheck second (fail-fast before policy),\n"
        "self_issue_constraint last (policy hook)."
    )
