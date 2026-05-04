"""Challenger contract test for `derive_verifier_event` (REQ-test-router-decision-contract-1777860546).

Independent black-box pin of the spec capability `router-decision-contract-tests`
(see `openspec/changes/REQ-test-router-decision-contract-1777860546/specs/...`).

Authored by the challenger stage from the spec only — no peeking at the dev's
parametrised test file in this same directory. One test function per spec
scenario (RDCT-S1 .. RDCT-S7) plus a few additional silent-escalate rows
called out in the proposal.md routing table that the spec text calls "every
silent-escalate case" without naming them individually.

Runs as a pure unit test: no DB, no network, no BKD client, no K8s.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from orchestrator.router import derive_verifier_event
from orchestrator.state import Event

# ─── helpers ─────────────────────────────────────────────────────────────


def _b64_tag(decision: dict[str, Any]) -> str:
    """Build a `decision:<base64-json>` tag exactly the way verifier-agent does.

    URL-safe base64, padding stripped — matches the shape the parser layer
    accepts (see verifier-decision-tag-fallback capability).
    """
    raw = json.dumps(decision, separators=(",", ":")).encode("utf-8")
    return "decision:" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _valid_pass() -> dict[str, Any]:
    return {
        "action": "pass",
        "fixer": None,
        "scope": None,
        "reason": "spec-conformant",
        "confidence": "high",
    }


def _valid_fix(fixer: str = "spec") -> dict[str, Any]:
    return {
        "action": "fix",
        "fixer": fixer,
        "scope": "openspec/",
        "reason": "drift",
        "confidence": "high",
    }


# ─── RDCT-S1 — runs as a unit test, no external deps ─────────────────────


def test_rdct_s1_no_external_dependencies():
    """The whole test module must be importable + invokable without any
    network / DB / BKD / K8s side effects. If this test runs at all under
    `pytest -m "not integration"`, S1 is satisfied — but pin it explicitly
    so future drift (e.g. someone adds an `async` PG fixture into the
    router import path) trips this case first.
    """
    event, decision, reason = derive_verifier_event(None, None)
    # Defensive shape — values asserted in S-defensive case below.
    assert isinstance(event, Event)
    assert decision is None or isinstance(decision, dict)
    assert isinstance(reason, str)


# ─── RDCT-S2 — happy path tag-base64 → per-stage pass ────────────────────


def test_rdct_s2_base64_pass_routes_to_stage_pass():
    tags = [_b64_tag(_valid_pass()), "verify:dev_cross_check"]
    event, decision, reason = derive_verifier_event(None, tags)
    assert event == Event.DEV_CROSS_CHECK_PASS, f"reason={reason!r}"
    assert decision is not None and decision["action"] == "pass"
    assert reason == ""


# ─── RDCT-S3 — plain `decision:pass` parser fallback (closes phona/sisyphus#371) ─


def test_rdct_s3_plain_decision_pass_tag_falls_back_to_stage_pass():
    """The exact shape that triggered the 5/4 v5 dogfood incident: a plain
    `decision:pass` tag without the base64 JSON. Post REQ-fix-verifier-
    decision-tag-1777812498 (closes phona/sisyphus#356) the parser layer
    synthesises a low-confidence decision so this is now a happy path.
    """
    tags = ["decision:pass", "verify:dev_cross_check"]
    event, decision, _reason = derive_verifier_event(None, tags)
    assert event == Event.DEV_CROSS_CHECK_PASS
    assert decision is not None
    assert decision["action"] == "pass"


# ─── RDCT-S4 — plain `decision:fail` is intentionally NOT in the alias table ─


def test_rdct_s4_plain_decision_fail_tag_escalates_with_no_json_reason():
    """`decision:fail` is not a synthesised happy path. The parser table only
    aliases pass / fix-dev / fix-spec / escalate / retry — `fail` falls
    through and surfaces as an escalate with `no decision JSON`.
    """
    tags = ["decision:fail", "verify:dev_cross_check"]
    event, _decision, reason = derive_verifier_event(None, tags)
    assert event == Event.VERIFY_ESCALATE
    assert "no decision JSON" in reason, f"got reason={reason!r}"


# ─── RDCT-S5 — schema-invalid base64 → escalate with `invalid decision` ──


def test_rdct_s5_schema_invalid_base64_escalates_with_invalid_decision_reason():
    """`{"action":"pass"}` is well-formed JSON but missing the required
    `confidence` field. The router must escalate with a reason naming the
    schema violation (so `verifier_parse_retry` can decide retry-worthiness
    from the reason text — see #372 telemetry).
    """
    bad_payload = base64.urlsafe_b64encode(b'{"action":"pass"}').decode("ascii").rstrip("=")
    tags = [f"decision:{bad_payload}", "verify:dev_cross_check"]
    event, _decision, reason = derive_verifier_event(None, tags)
    assert event == Event.VERIFY_ESCALATE
    assert "invalid decision" in reason, f"got reason={reason!r}"


# ─── RDCT-S6 — schema-valid base64 but verify:<unknown_stage> escalates ──


def test_rdct_s6_unknown_verifier_stage_escalates_even_with_valid_decision():
    """The decision JSON itself is fine; the `verify:<stage>` tag points at
    a stage that has no entry in the router's pass-routing table. Escalate
    rather than silently routing to an arbitrary default.
    """
    tags = [_b64_tag(_valid_pass()), "verify:unknown_stage"]
    event, decision, reason = derive_verifier_event(None, tags)
    assert event == Event.VERIFY_ESCALATE
    assert decision is not None and decision["action"] == "pass"
    assert "unknown verifier stage" in reason, f"got reason={reason!r}"


# ─── RDCT-S7 — base64 tag wins precedence over plain decision:* tag ──────


def test_rdct_s7_base64_decision_wins_over_plain_decision_tag():
    """When BOTH a base64 and a plain decision tag are present, the base64
    one (full schema, includes `fixer`) must win. Otherwise an agent that
    appends `decision:pass` for legibility on top of a base64 fix decision
    would cause the router to silently flip the action.
    """
    fix_payload = _valid_fix(fixer="spec")
    tags = [_b64_tag(fix_payload), "decision:pass", "verify:dev_cross_check"]
    event, decision, _reason = derive_verifier_event(None, tags)
    assert event == Event.VERIFY_FIX_NEEDED
    assert decision is not None
    # The base64 payload's `fixer` survived — not the synthesised null from
    # the plain `decision:pass` alias.
    assert decision.get("fixer") == "spec"
    assert decision["action"] == "fix"


# ─── Additional silent-escalate rows from proposal.md routing table ──────
#
# The spec prose calls these out as "every silent-escalate case". They aren't
# numbered scenarios but the proposal table pins them, and the whole point
# of phona/sisyphus#371 is that any of them silently flipping to
# VERIFY_ESCALATE is the failure mode we're guarding against.


def test_defensive_none_inputs_escalate_without_crash():
    """Spec proposal table row: `nothing parseable anywhere` → VERIFY_ESCALATE
    reason=`no decision JSON`. Defensive — must not raise on None / None.
    """
    event, decision, reason = derive_verifier_event(None, None)
    assert event == Event.VERIFY_ESCALATE
    assert decision is None
    assert "no decision JSON" in reason


def test_empty_tag_list_escalates_without_crash():
    """Same row as above but with `tags=[]` rather than `tags=None`."""
    event, decision, reason = derive_verifier_event(None, [])
    assert event == Event.VERIFY_ESCALATE
    assert decision is None
    assert "no decision JSON" in reason


def test_valid_decision_without_any_verify_stage_tag_escalates():
    """Spec proposal table row: `valid decision but no verify:<stage> tag`
    → VERIFY_ESCALATE reason=`unknown verifier stage`.
    """
    tags = [_b64_tag(_valid_pass())]
    event, _decision, reason = derive_verifier_event(None, tags)
    assert event == Event.VERIFY_ESCALATE
    assert "unknown verifier stage" in reason, f"got reason={reason!r}"


def test_base64_that_decodes_but_is_not_json_escalates():
    """Spec proposal table row: `base64 that decodes but isn't valid JSON`
    → VERIFY_ESCALATE reason=`no decision JSON`.
    """
    not_json = base64.urlsafe_b64encode(b"this is not json").decode("ascii").rstrip("=")
    tags = [f"decision:{not_json}", "verify:dev_cross_check"]
    event, _decision, reason = derive_verifier_event(None, tags)
    assert event == Event.VERIFY_ESCALATE
    assert "no decision JSON" in reason, f"got reason={reason!r}"


def test_base64_fix_routes_to_verify_fix_needed_regardless_of_stage():
    """Cross-stage smoke for the fix path: a base64 fix decision should
    still surface as VERIFY_FIX_NEEDED on a different known stage, since
    the per-stage routing table only governs the pass branch.
    """
    fix_payload = _valid_fix(fixer="dev")
    for stage in ("verify:staging_test", "verify:pr_ci", "verify:spec_lint"):
        tags = [_b64_tag(fix_payload), stage]
        event, decision, _reason = derive_verifier_event(None, tags)
        assert event == Event.VERIFY_FIX_NEEDED, f"stage={stage}"
        assert decision is not None and decision["action"] == "fix"
        assert decision.get("fixer") == "dev"


# ─── Sanity: contract test imports succeed without any orch boot side-effects ─


def test_module_imports_are_pure():
    """Reading `from orchestrator.router import derive_verifier_event` must
    not require Settings / DB / BKD secrets — `conftest.py` only sets a
    handful of dummy env vars, so anything heavier breaks here first.
    """
    # If the import at module top failed, pytest would never have collected
    # this test. The fact that we get here is the assertion.
    assert callable(derive_verifier_event)
    assert hasattr(Event, "VERIFY_ESCALATE")
    assert hasattr(Event, "VERIFY_FIX_NEEDED")
    assert hasattr(Event, "DEV_CROSS_CHECK_PASS")


# ─── Deliberately not pulled in: ─────────────────────────────────────────
# - decision_to_event / validate_decision / extract_decision_from_issue are
#   internal-by-convention helpers — black-box contract is `derive_verifier_event`.
# - derive_verifier_event_with_retry_info is a wrapper that adds a retry-worthiness
#   bool; that's #372 territory, not this REQ.
# - description ` ```json``` ` block parsing is exercised by test_verifier.py
#   already; we don't duplicate it here so the contract surface stays minimal.

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
