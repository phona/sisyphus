"""Contract test for `derive_verifier_event` decision parsing (closes phona/sisyphus#371).

Pin the verifier session.completed → Event mapping so future drift in the
verifier output format / parser fallback layers fails at unit-test time, not
only when a live REQ silently sticks at ESCALATED.

Origin incident: 5/4 v5 dogfood — verifier-agent emitted a plain
`decision:pass` tag (no base64 JSON), router's old parser found nothing
parseable and routed VERIFY_ESCALATE without surfacing the failure.
REQ-fix-verifier-decision-tag-1777812498 (closes #356) added the plain
`decision:<action>[-<fixer>]` tag fallback in `verifier_parser`; this test
locks both the happy paths AND the silent-escalate paths so neither side
of the contract regresses.

Note: the cases below pin **current** post-#356 behavior. Issue #371 listed
expectations from before the fallback landed (e.g. plain `decision:pass`
escalating); those have been updated in place to the now-correct routes,
with comments where the case differs from the original sketch.
"""
from __future__ import annotations

import base64
import json

import pytest

from orchestrator.router import derive_verifier_event
from orchestrator.state import Event


def _b64(d: dict) -> str:
    """Standard base64, no padding — same shape verifier-agent emits."""
    return base64.b64encode(json.dumps(d).encode()).decode().rstrip("=")


_VALID_PASS = {"action": "pass", "fixer": None, "reason": "ok", "confidence": "high"}
_VALID_FIX_DEV = {"action": "fix", "fixer": "dev", "reason": "bug", "confidence": "high"}


CASES: list[tuple[str, str | None, list[str], Event, str]] = [
    # (label, description, tags, expected_event, expected_reason_substring)
    #
    # ─── 1) tag base64 happy paths ────────────────────────────────────────
    (
        "tag-base64 valid pass + dev_cross_check stage → DEV_CROSS_CHECK_PASS",
        None,
        [f"decision:{_b64(_VALID_PASS)}", "verify:dev_cross_check"],
        Event.DEV_CROSS_CHECK_PASS,
        "",
    ),
    (
        "tag-base64 valid fix-dev → VERIFY_FIX_NEEDED",
        None,
        [f"decision:{_b64(_VALID_FIX_DEV)}", "verify:dev_cross_check"],
        Event.VERIFY_FIX_NEEDED,
        "",
    ),
    # ─── 2) plain decision:<action> tag fallback (post-#356) ──────────────
    # Was VERIFY_ESCALATE pre-#356 (the v5 incident); now routes via parser
    # synthesis to the matching pass/fix/escalate/retry event.
    (
        "plain decision:pass tag → DEV_CROSS_CHECK_PASS (orch-fallback synth)",
        None,
        ["decision:pass", "verify:dev_cross_check"],
        Event.DEV_CROSS_CHECK_PASS,
        "",
    ),
    (
        "plain decision:fix-dev tag → VERIFY_FIX_NEEDED (orch-fallback synth)",
        None,
        ["decision:fix-dev", "verify:dev_cross_check"],
        Event.VERIFY_FIX_NEEDED,
        "",
    ),
    (
        "plain decision:escalate tag → VERIFY_ESCALATE (intentional)",
        None,
        ["decision:escalate", "verify:dev_cross_check"],
        Event.VERIFY_ESCALATE,
        "",
    ),
    (
        "plain decision:retry tag → VERIFY_INFRA_RETRY",
        None,
        ["decision:retry", "verify:dev_cross_check"],
        Event.VERIFY_INFRA_RETRY,
        "",
    ),
    # ─── 3) silent-escalate paths (the regressions we're guarding) ────────
    # `decision:fail` is NOT in `_PLAIN_TAG_TO_DECISION`; no decision can be
    # synthesized → router escalates with explicit reason. This pins that
    # adding `decision:fail` to the alias table later would be a contract
    # change, not a bugfix.
    (
        "plain decision:fail tag → VERIFY_ESCALATE (no synth, pinned)",
        None,
        ["decision:fail", "verify:dev_cross_check"],
        Event.VERIFY_ESCALATE,
        "no decision JSON",
    ),
    # base64 of `{"action":"pass"}` — parses as JSON but fails schema
    # (missing required `confidence`). The test in #371 used this exact
    # payload; the reason wording is "invalid decision: <why>".
    (
        "tag-base64 partial schema (missing confidence) → VERIFY_ESCALATE invalid decision",
        None,
        ["decision:eyJhY3Rpb24iOiJwYXNzIn0=", "verify:dev_cross_check"],
        Event.VERIFY_ESCALATE,
        "invalid decision",
    ),
    # base64 of `{"x":1~}` — base64 decodes but the result is not valid JSON.
    # Parser's `_extract_from_tags` records a failed attempt; nothing else
    # provides a decision; raw doesn't contain "action" → not retry-worthy.
    (
        "tag-base64 garbled JSON → VERIFY_ESCALATE no decision JSON",
        None,
        ["decision:eyJ4Ijoxfn0=", "verify:dev_cross_check"],
        Event.VERIFY_ESCALATE,
        "no decision JSON",
    ),
    # ─── 4) description-only path (agent forgot the tag) ──────────────────
    (
        "description ```json``` block with no tag → DEV_CROSS_CHECK_PASS",
        '```json\n{"action":"pass","fixer":null,"reason":"x","confidence":"high"}\n```',
        ["verify:dev_cross_check"],
        Event.DEV_CROSS_CHECK_PASS,
        "",
    ),
    # ─── 5) unknown verifier stage → escalate with named reason ───────────
    (
        "valid decision but unknown verify:<stage> → VERIFY_ESCALATE unknown stage",
        None,
        [f"decision:{_b64(_VALID_PASS)}", "verify:unknown_stage"],
        Event.VERIFY_ESCALATE,
        "unknown verifier stage",
    ),
    # ─── 6) nothing at all → escalate, no decision ────────────────────────
    (
        "no tag and no description JSON → VERIFY_ESCALATE no decision JSON",
        "boring agent message with zero JSON",
        ["verify:dev_cross_check"],
        Event.VERIFY_ESCALATE,
        "no decision JSON",
    ),
]


@pytest.mark.parametrize(
    ("label", "description", "tags", "expected_event", "expected_reason_kw"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_derive_verifier_event_contract(
    label: str,
    description: str | None,
    tags: list[str],
    expected_event: Event,
    expected_reason_kw: str,
) -> None:
    event, _decision, reason = derive_verifier_event(description, tags)
    assert event == expected_event, f"{label}: event {event} != {expected_event} (reason={reason!r})"
    if expected_reason_kw:
        assert expected_reason_kw in reason, (
            f"{label}: expected reason to contain {expected_reason_kw!r}, got {reason!r}"
        )
    else:
        assert reason == "", f"{label}: expected empty reason on happy path, got {reason!r}"


def test_pass_decision_without_stage_tag_escalates() -> None:
    """A valid pass decision but no `verify:<stage>` tag → unknown stage escalate.

    Defends against the case where a verifier issue PATCHes its decision tag
    correctly but never carried a `verify:<stage>` tag at all (router can't
    pick a pass route, must escalate rather than guess).
    """
    event, decision, reason = derive_verifier_event(None, [f"decision:{_b64(_VALID_PASS)}"])
    assert event == Event.VERIFY_ESCALATE
    assert decision is not None
    assert decision["action"] == "pass"
    assert "unknown verifier stage" in reason


def test_empty_inputs_give_no_decision_escalate() -> None:
    """Defensive: None / empty inputs land in VERIFY_ESCALATE, not a crash."""
    event, decision, reason = derive_verifier_event(None, None)
    assert event == Event.VERIFY_ESCALATE
    assert decision is None
    assert "no decision JSON" in reason


def test_base64_tag_wins_over_plain_tag() -> None:
    """Both `decision:<base64>` and plain `decision:<action>` present → base64 wins.

    Pinned because adding aliases shouldn't change precedence: the tag-base64
    happy path is the authoritative format that all other layers fall back from.
    """
    valid_fix = {"action": "fix", "fixer": "spec", "reason": "spec drift", "confidence": "high"}
    event, decision, _reason = derive_verifier_event(
        None,
        [f"decision:{_b64(valid_fix)}", "decision:pass", "verify:dev_cross_check"],
    )
    assert event == Event.VERIFY_FIX_NEEDED
    assert decision is not None
    assert decision["fixer"] == "spec"
