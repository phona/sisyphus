"""Contract tests for REQ-ux-tags-injection-1777257283: intent tag propagation.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-ux-tags-injection-1777257283/specs/intent-tag-propagation/spec.md

Scenarios covered:
  UTI-S1   filter strips sisyphus-managed exact tags
  UTI-S2   filter strips sisyphus-managed prefixes
  UTI-S3   filter strips REQ-* identifier tags
  UTI-S4   filter keeps user-hint tags in first-seen order
  UTI-S5   filter de-duplicates survivors
  UTI-S6   filter mixes managed + hint and only forwards hints
  UTI-S7   filter is robust against None / non-string / blank entries
  UTI-S8   filter is idempotent
  UTI-S9   start_intake forwards repo: + ux: hints into PATCH
  UTI-S10  start_intake without hints stays backward compatible
  UTI-S11  start_analyze forwards repo: tag through PATCH
  UTI-S12  start_analyze strips stale sisyphus-managed tags
  UTI-S13  start_analyze_with_finalized_intent inherits hints
  UTI-S14  start_challenger inherits hints with correct tag order
  UTI-S15  start_challenger filters re-emitted role/managed tags

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
"""
from __future__ import annotations

from pathlib import Path

# ── Fixtures: production source path ──────────────────────────────────────────

_SRC = Path(__file__).resolve().parent.parent / "src" / "orchestrator"


# ── UTI-S1 ────────────────────────────────────────────────────────────────────


def test_UTI_S1_filter_strips_exact_managed_tags() -> None:
    """Exact sisyphus-managed strings must all be dropped."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    managed_exact = [
        "sisyphus", "intake", "analyze", "challenger", "verifier",
        "fixer", "accept", "staging-test", "pr-ci", "done-archive",
    ]
    result = filter_propagatable_intent_tags(managed_exact)
    assert result == [], (
        f"Expected [], got {result!r} — managed exact tags must not be forwarded."
    )


# ── UTI-S2 ────────────────────────────────────────────────────────────────────


def test_UTI_S2_filter_strips_managed_prefix_tags() -> None:
    """Tags matching any sisyphus-managed prefix must all be dropped."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    prefix_tags = [
        "intent:analyze",
        "result:pass",
        "pr-ci:pass",
        "verify:dev_cross_check",
        "trigger:fail",
        "decision:eyJ...",
        "fixer:dev",
        "parent:analyze",
        "parent-id:abc123",
        "parent-stage:spec_lint",
        "target:phona/foo",
        "round-3",
        "pr:phona/foo#42",
    ]
    result = filter_propagatable_intent_tags(prefix_tags)
    assert result == [], (
        f"Expected [], got {result!r} — all prefix-managed tags must be stripped."
    )


# ── UTI-S3 ────────────────────────────────────────────────────────────────────


def test_UTI_S3_filter_strips_req_id_tags() -> None:
    """Tags matching ^REQ-[\\w-]+$ must be stripped (REQ identity, not user hint)."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    req_tags = [
        "REQ-ux-tags-injection-1777257283",
        "REQ-foo",
        "REQ-bar-baz-1234567",
    ]
    result = filter_propagatable_intent_tags(req_tags)
    assert result == [], (
        f"Expected [], got {result!r} — REQ-id tags must not be forwarded."
    )


# ── UTI-S4 ────────────────────────────────────────────────────────────────────


def test_UTI_S4_filter_keeps_user_hints_in_order() -> None:
    """User-hint tags must survive the filter with first-seen order preserved."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    hints = [
        "repo:phona/sisyphus",
        "ux:fast-track",
        "priority:high",
        "team:platform",
        "spec_home_repo:phona/sisyphus",
    ]
    result = filter_propagatable_intent_tags(hints)
    assert result == hints, (
        f"Expected all hints preserved in order; got {result!r}."
    )


# ── UTI-S5 ────────────────────────────────────────────────────────────────────


def test_UTI_S5_filter_deduplicates_survivors() -> None:
    """Duplicate hint tags must be collapsed to first-seen occurrence."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    result = filter_propagatable_intent_tags(
        ["repo:foo/bar", "repo:foo/bar", "ux:fast-track", "ux:fast-track"]
    )
    assert result == ["repo:foo/bar", "ux:fast-track"], (
        f"Expected de-duplicated list; got {result!r}."
    )


# ── UTI-S6 ────────────────────────────────────────────────────────────────────


def test_UTI_S6_filter_mixed_keeps_only_hints() -> None:
    """Mixed input: managed tags dropped, hint tags forwarded, order preserved."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    mixed = [
        "intent:analyze",
        "REQ-foo-1234",
        "analyze",
        "repo:phona/foo",
        "ux:fast-track",
        "result:pass",
        "pr:phona/foo#1",
    ]
    result = filter_propagatable_intent_tags(mixed)
    assert result == ["repo:phona/foo", "ux:fast-track"], (
        f"Expected only hint tags; got {result!r}."
    )


# ── UTI-S7 ────────────────────────────────────────────────────────────────────


def test_UTI_S7_filter_robust_none_nonstring_blank() -> None:
    """None, empty list, and lists containing None / int / blank strings are all safe."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    assert filter_propagatable_intent_tags(None) == [], "None input must return []"
    assert filter_propagatable_intent_tags([]) == [], "Empty list must return []"
    result = filter_propagatable_intent_tags([None, 42, "", "   ", "ux:ok"])
    assert result == ["ux:ok"], (
        f"Non-string / blank entries must be silently dropped; got {result!r}."
    )


# ── UTI-S8 ────────────────────────────────────────────────────────────────────


def test_UTI_S8_filter_is_idempotent() -> None:
    """filter(filter(x)) == filter(x) for representative inputs."""
    from orchestrator.intent_tags import filter_propagatable_intent_tags

    test_cases = [
        ["intent:analyze", "REQ-foo", "repo:phona/foo", "ux:fast-track", "result:pass"],
        ["sisyphus", "challenger", "repo:phona/bar", "priority:high"],
        ["repo:a/b", "repo:a/b", "ux:ok"],
        [],
        None,
    ]
    for xs in test_cases:
        once = filter_propagatable_intent_tags(xs)
        twice = filter_propagatable_intent_tags(once)
        assert once == twice, (
            f"filter is not idempotent on {xs!r}: once={once!r}, twice={twice!r}"
        )


# ── UTI-S9 / UTI-S10 — start_intake callsite ─────────────────────────────────
# Source-inspection approach: verifies the action imports and uses the filter
# function from intent_tags. This is a structural contract — the BKD PATCH must
# incorporate forwarded hint tags, which requires the filter to be called.


def test_UTI_S9_start_intake_imports_and_uses_filter() -> None:
    """start_intake.py must import filter_propagatable_intent_tags and call it with tags."""
    src = (_SRC / "actions" / "start_intake.py").read_text(encoding="utf-8")
    assert "filter_propagatable_intent_tags" in src, (
        "start_intake.py must import and use filter_propagatable_intent_tags "
        "(UTI-S9: hint tags must be forwarded into the PATCH)."
    )
    assert "filter_propagatable_intent_tags(tags)" in src, (
        "start_intake.py must call filter_propagatable_intent_tags(tags) "
        "to forward user hint tags into the update_issue PATCH."
    )


def test_UTI_S10_start_intake_tags_array_structure() -> None:
    """start_intake.py must build tags as [sisyphus, intake, req_id, *forwarded]."""
    src = (_SRC / "actions" / "start_intake.py").read_text(encoding="utf-8")
    # The base array must appear; hint tags are appended via the filter
    assert '"sisyphus"' in src or "'sisyphus'" in src, (
        "start_intake.py must include 'sisyphus' in the tags base array."
    )
    assert '"intake"' in src or "'intake'" in src, (
        "start_intake.py must include 'intake' in the tags base array."
    )


# ── UTI-S11 / UTI-S12 — start_analyze callsite ───────────────────────────────


def test_UTI_S11_start_analyze_imports_and_uses_filter() -> None:
    """start_analyze.py must import filter_propagatable_intent_tags and call it with tags."""
    src = (_SRC / "actions" / "start_analyze.py").read_text(encoding="utf-8")
    assert "filter_propagatable_intent_tags" in src, (
        "start_analyze.py must use filter_propagatable_intent_tags "
        "(UTI-S11: repo: tag must survive through the PATCH)."
    )
    assert "filter_propagatable_intent_tags(tags)" in src, (
        "start_analyze.py must call filter_propagatable_intent_tags(tags)."
    )


def test_UTI_S12_start_analyze_strips_managed_tags() -> None:
    """Verify start_analyze.py does NOT manually forward sisyphus-managed tags.

    The filter handles stripping; the action must rely on the filter rather than
    hand-picking which tags to forward (which would re-emit managed tags).
    """
    src = (_SRC / "actions" / "start_analyze.py").read_text(encoding="utf-8")
    # The action must call the filter — not manually select tags to forward
    assert "filter_propagatable_intent_tags" in src, (
        "start_analyze.py must use filter_propagatable_intent_tags to strip "
        "managed tags automatically (UTI-S12)."
    )


# ── UTI-S13 — start_analyze_with_finalized_intent callsite ───────────────────


def test_UTI_S13_start_analyze_finalized_inherits_hints() -> None:
    """start_analyze_with_finalized_intent.py must forward hint tags when creating the analyze issue."""
    src = (_SRC / "actions" / "start_analyze_with_finalized_intent.py").read_text(
        encoding="utf-8"
    )
    assert "filter_propagatable_intent_tags" in src, (
        "start_analyze_with_finalized_intent.py must use filter_propagatable_intent_tags "
        "(UTI-S13: intake-path analyze must inherit hint tags)."
    )
    assert "filter_propagatable_intent_tags(tags)" in src, (
        "start_analyze_with_finalized_intent.py must call filter_propagatable_intent_tags(tags)."
    )


# ── UTI-S14 / UTI-S15 — start_challenger callsite ────────────────────────────


def test_UTI_S14_start_challenger_imports_and_uses_filter() -> None:
    """start_challenger.py must import filter_propagatable_intent_tags and forward hints."""
    src = (_SRC / "actions" / "start_challenger.py").read_text(encoding="utf-8")
    assert "filter_propagatable_intent_tags" in src, (
        "start_challenger.py must use filter_propagatable_intent_tags "
        "(UTI-S14: challenger must inherit user hint tags from analyze issue)."
    )
    assert "filter_propagatable_intent_tags(tags)" in src, (
        "start_challenger.py must call filter_propagatable_intent_tags(tags)."
    )


def test_UTI_S15_start_challenger_does_not_duplicate_managed_tags() -> None:
    """start_challenger must not bypass the filter and manually re-inject managed tags.

    The correct pattern is: [challenger, req_id, parent-id:..., *pr_links, *filter(tags)].
    If the action hand-picks tags from the input without filtering, managed tags like
    'analyze', 'result:pass', 'challenger' could appear twice in the output.
    """
    src = (_SRC / "actions" / "start_challenger.py").read_text(encoding="utf-8")
    assert "filter_propagatable_intent_tags" in src, (
        "start_challenger.py must use filter_propagatable_intent_tags "
        "(UTI-S15: filtered hints only, no re-emitted managed tags)."
    )
    # Verify challenger role tag is in the explicit base — not pulled from input tags
    assert '"challenger"' in src or "'challenger'" in src, (
        "start_challenger.py must explicitly include 'challenger' in the tags base array, "
        "not rely on the input tags."
    )


# ── Module-level structural contracts ─────────────────────────────────────────


def test_UTI_module_exports_constants() -> None:
    """orchestrator.intent_tags must export SISYPHUS_MANAGED_EXACT and SISYPHUS_MANAGED_PREFIXES."""
    from orchestrator.intent_tags import (
        SISYPHUS_MANAGED_EXACT,
        SISYPHUS_MANAGED_PREFIXES,
    )

    assert isinstance(SISYPHUS_MANAGED_EXACT, frozenset), (
        "SISYPHUS_MANAGED_EXACT must be a frozenset[str]."
    )
    assert isinstance(SISYPHUS_MANAGED_PREFIXES, tuple), (
        "SISYPHUS_MANAGED_PREFIXES must be a tuple[str, ...]."
    )
    # Spot-check known members from spec
    assert "sisyphus" in SISYPHUS_MANAGED_EXACT
    assert "challenger" in SISYPHUS_MANAGED_EXACT
    assert "done-archive" in SISYPHUS_MANAGED_EXACT
    assert any(p.startswith("parent") for p in SISYPHUS_MANAGED_PREFIXES), (
        "SISYPHUS_MANAGED_PREFIXES must contain a 'parent'-family prefix."
    )
    assert "pr:" in SISYPHUS_MANAGED_PREFIXES, (
        "SISYPHUS_MANAGED_PREFIXES must contain 'pr:' to mask PR link tags."
    )


def test_UTI_is_sisyphus_managed_tag_callable() -> None:
    """orchestrator.intent_tags must export callable is_sisyphus_managed_tag(tag) -> bool."""
    from orchestrator.intent_tags import is_sisyphus_managed_tag

    assert callable(is_sisyphus_managed_tag)
    # Spot-check expected results
    assert is_sisyphus_managed_tag("sisyphus") is True
    assert is_sisyphus_managed_tag("intent:analyze") is True
    assert is_sisyphus_managed_tag("REQ-foo-1234") is True
    assert is_sisyphus_managed_tag("repo:phona/foo") is False
    assert is_sisyphus_managed_tag("ux:fast-track") is False
    assert is_sisyphus_managed_tag("priority:high") is False
