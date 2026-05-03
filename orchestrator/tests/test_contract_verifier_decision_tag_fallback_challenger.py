"""Challenger contract tests for REQ-fix-verifier-decision-tag-1777812498.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-verifier-decision-tag-1777812498/specs/
    verifier-decision-tag-fallback/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation
instead. If a test is truly wrong, escalate to spec_fixer to correct the
spec, not the test.

Capability: verifier-decision-tag-fallback

Scenarios covered:
  VDTF-S1  every rendered verifier prompt mandates a `decision:<action>` BKD
           tag with a literal example and a `curl PATCH` tag-merge example.
  VDTF-S2  plain `decision:pass` tag with no JSON in text yields a synthesized
           pass decision (action=pass, fixer=None, confidence=low,
           reason starting with "orch-fallback") that validates as ok via
           router.validate_decision.
  VDTF-S3  plain `decision:fix` tag without `-dev` / `-spec` suffix is NOT
           synthesized (decision is None) and validate_decision rejects it.

Modules under contract:
  orchestrator.prompts.render
  orchestrator.verifier_parser.extract_decision_robust
  orchestrator.router.validate_decision
"""
from __future__ import annotations

import re

import pytest


# ─── VDTF-S1: prompt mandate ──────────────────────────────────────────────────

# (stage, trigger) pairs the spec applies to: every verifier prompt the
# orchestrator can render, mirroring the prompts/verifier/*_*.md.j2 set.
_VERIFIER_STAGES = (
    "accept",
    "analyze",
    "analyze_artifact_check",
    "challenger",
    "dev_cross_check",
    "pr_ci",
    "spec_lint",
    "staging_test",
)
_VERIFIER_TRIGGERS = ("success", "fail")

_LITERAL_TAG_EXAMPLES = (
    "decision:pass",
    "decision:fix-dev",
    "decision:fix-spec",
    "decision:escalate",
    "decision:retry",
)


def _render_verifier_prompt(stage: str, trigger: str) -> str:
    """Render verifier/<stage>_<trigger>.md.j2 with a generous neutral context.

    The prompt suite expects assorted context vars; we pass a superset so any
    prompt can render without KeyError. The point of VDTF-S1 is to assert
    *what's in the rendered text*, not to drive any specific branch.
    """
    from orchestrator.prompts import render

    template_name = f"verifier/{stage}_{trigger}.md.j2"
    return render(
        template_name,
        req_id="REQ-vdtf-contract-test",
        stage=stage,
        trigger=trigger,
        artifact_paths=[],
        stderr_tail="",
        history=[],
        project_id="proj-vdtf",
        project_alias="proj-vdtf",
        checker_stdout="",
        checker_stderr="",
        checker_exit_code=0,
    )


@pytest.mark.parametrize("stage", _VERIFIER_STAGES)
@pytest.mark.parametrize("trigger", _VERIFIER_TRIGGERS)
def test_vdtf_s1_prompt_mandates_decision_tag_with_example(stage, trigger):
    """VDTF-S1: rendered verifier prompt MUST contain the mandatory tag
    instruction, at least one literal `decision:<x>` example, and a curl PATCH
    tag-merge example.
    """
    rendered = _render_verifier_prompt(stage, trigger)

    # 1. At least one literal `decision:<action>[-<fixer>]` example present.
    found_literal = [ex for ex in _LITERAL_TAG_EXAMPLES if ex in rendered]
    assert found_literal, (
        f"verifier/{stage}_{trigger}.md.j2 does not contain any literal "
        f"`decision:<action>` example. Spec VDTF-S1 requires at least one "
        f"of: {list(_LITERAL_TAG_EXAMPLES)}."
    )

    # 2. A curl PATCH example showing the tag-merge pattern. Tag merge means
    # the example illustrates updating tags (PATCH … tags …) — same shape as
    # other tag-mutation examples in the prompt suite.
    has_curl_patch_tags = bool(
        re.search(
            r"curl[^\n]*(?:-X\s*PATCH|--request\s+PATCH)[\s\S]*?\"tags\"\s*:",
            rendered,
        )
    )
    assert has_curl_patch_tags, (
        f"verifier/{stage}_{trigger}.md.j2 lacks a `curl PATCH … tags…` "
        "example. Spec VDTF-S1 requires the same tag-merge curl example "
        "pattern used elsewhere in the prompt suite."
    )

    # 3. The instruction must be presented as a hard requirement (HARD
    # CONSTRAINT level per the proposal/spec language).
    assert re.search(r"HARD\s*CONSTRAINT|MUST|必须", rendered), (
        f"verifier/{stage}_{trigger}.md.j2 does not present the decision-tag "
        "instruction at HARD CONSTRAINT / MUST level."
    )


# ─── VDTF-S2: synthesized pass decision from plain tag ────────────────────────


def test_vdtf_s2_plain_decision_pass_tag_yields_synthesized_decision():
    """VDTF-S2: GIVEN tags=[..., "decision:pass"] and description with no
    JSON code block, WHEN extract_decision_robust runs, THEN ParseResult
    .decision == {action=pass, fixer=None, confidence=low, reason starts
    with "orch-fallback"}, AND router.validate_decision returns ok.
    """
    from orchestrator import router
    from orchestrator.verifier_parser import extract_decision_robust

    description = (
        "The verifier finished but did not emit a JSON decision block.\n"
        "Some prose summary, no fenced ``` here, nothing parseable.\n"
    )
    tags = ["verifier", "verify:staging_test", "decision:pass"]

    result = extract_decision_robust(description, tags)

    assert result.decision is not None, (
        "Spec VDTF-S2: expected a synthesized decision dict from the plain "
        "`decision:pass` tag; got None."
    )
    decision = result.decision
    assert decision.get("action") == "pass", decision
    assert decision.get("fixer") is None, decision
    assert decision.get("confidence") == "low", decision
    reason = decision.get("reason", "")
    assert isinstance(reason, str) and reason.startswith("orch-fallback"), (
        f"Spec VDTF-S2: reason must start with 'orch-fallback'; got {reason!r}"
    )

    ok, why = router.validate_decision(decision)
    assert ok, f"Spec VDTF-S2: synthesized decision must validate ok; got {why!r}"


# ─── VDTF-S3: plain decision:fix without fixer suffix is NOT synthesized ──────


def test_vdtf_s3_plain_decision_fix_without_suffix_is_not_synthesized():
    """VDTF-S3: GIVEN tags=["verifier", "decision:fix"] (no -dev / -spec) and
    description without JSON, WHEN extract_decision_robust runs, THEN
    ParseResult.decision is None (parser declines to guess fixer), AND
    validate_decision(result.decision) returns ok=False.
    """
    from orchestrator import router
    from orchestrator.verifier_parser import extract_decision_robust

    description = "Verifier prose only. No JSON. Nothing to parse."
    tags = ["verifier", "decision:fix"]

    result = extract_decision_robust(description, tags)

    assert result.decision is None, (
        "Spec VDTF-S3: a plain `decision:fix` tag without `-dev`/`-spec` "
        "suffix MUST NOT be synthesized into a decision; the parser must "
        f"decline to guess. Got: {result.decision!r}"
    )

    ok, _why = router.validate_decision(result.decision)
    assert not ok, (
        "Spec VDTF-S3: with no synthesized decision the result MUST validate "
        "as not ok (preserving the existing VERIFY_ESCALATE route)."
    )
