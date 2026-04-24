"""
Contract tests for INTAKING stage — REQ-intaking-1777003852.
Challenger-agent: written from spec, not from dev implementation.

Contracts (spec source: analyze issue rc1727nt):

  C1  intent:intake tag (without intake) on issue.updated → INTENT_INTAKE
  C2  intake already handled (intent:intake + intake) → None
  C3  session.completed + intake + result:pass → INTAKE_PASS
  C4  session.completed + intake + result:fail → INTAKE_FAIL
  C5  session.completed + intake only (no result:*) → None (intermediate round)
  C6  ReqState.INTAKING.value == "intaking"
  C7  Event.INTENT_INTAKE.value == "intent.intake"
  C8  Event.INTAKE_PASS.value == "intake.pass"
  C9  Event.INTAKE_FAIL.value == "intake.fail"
  C10 INIT + INTENT_INTAKE → INTAKING, action "start_intake"
  C11 INTAKING + INTAKE_PASS → ANALYZING, action "start_analyze_with_finalized_intent"
  C12 INTAKING + INTAKE_FAIL → ESCALATED, action "escalate"
  C13 extract_intake_finalized_intent: valid 6-field JSON codeblock → dict
  C14 extract_intake_finalized_intent: partial JSON (missing fields) → None
  C15 extract_intake_finalized_intent: empty string → None
  C16 start_intake action is registered in the action registry
  C17 start_analyze_with_finalized_intent action is registered
"""
from __future__ import annotations

import pytest

from orchestrator.router import derive_event
from orchestrator.state import Event, ReqState, decide


# ──────────────────────────────────────────────────────────────────────────────
# C1–C5  Router: derive_event contracts
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("event_type,tags,expected", [
    # C1: fresh intent:intake → start INTAKING
    ("issue.updated",     ["intent:intake"],                          Event.INTENT_INTAKE),
    # C2: already handled (intake tag present) → suppress re-dispatch
    ("issue.updated",     ["intent:intake", "intake", "REQ-1"],       None),
    # C3: intake agent completed with pass
    ("session.completed", ["intake", "REQ-1", "result:pass"],         Event.INTAKE_PASS),
    # C4: intake agent completed with fail
    ("session.completed", ["intake", "REQ-1", "result:fail"],         Event.INTAKE_FAIL),
    # C5: intake intermediate (multi-turn, no result yet) → no state change
    ("session.completed", ["intake", "REQ-1"],                        None),
])
def test_contract_router_intake_events(event_type, tags, expected):
    assert derive_event(event_type, tags) == expected


# ──────────────────────────────────────────────────────────────────────────────
# C6–C9  Enum value contracts — naming spec is a hard requirement
# ──────────────────────────────────────────────────────────────────────────────

def test_contract_reqstate_intaking_value():
    # C6: spec mandates lowercase singular "intaking"
    assert ReqState.INTAKING.value == "intaking"


def test_contract_event_values():
    # C7–C9: spec mandates dot-separated lowercase event values
    assert Event.INTENT_INTAKE.value == "intent.intake"
    assert Event.INTAKE_PASS.value == "intake.pass"
    assert Event.INTAKE_FAIL.value == "intake.fail"


# ──────────────────────────────────────────────────────────────────────────────
# C10–C12  State transition contracts
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state,event,next_state,action", [
    # C10
    (ReqState.INIT,     Event.INTENT_INTAKE, ReqState.INTAKING,  "start_intake"),
    # C11
    (ReqState.INTAKING, Event.INTAKE_PASS,   ReqState.ANALYZING, "start_analyze_with_finalized_intent"),
    # C12
    (ReqState.INTAKING, Event.INTAKE_FAIL,   ReqState.ESCALATED, "escalate"),
])
def test_contract_state_transitions(state, event, next_state, action):
    t = decide(state, event)
    assert t is not None, f"missing transition {state.value}+{event.value}"
    assert t.next_state == next_state, f"wrong next_state for {state.value}+{event.value}"
    assert t.action == action, f"wrong action for {state.value}+{event.value}"


# ──────────────────────────────────────────────────────────────────────────────
# C13–C15  extract_intake_finalized_intent: JSON extraction contracts
# ──────────────────────────────────────────────────────────────────────────────

VALID_FINALIZED_INTENT_JSON = """
```json
{
  "involved_repos": ["phona/sisyphus"],
  "business_behavior": "INTAKING stage isolates brainstorm from implementation",
  "data_constraints": "finalized intent JSON must have 6 required fields",
  "edge_cases": "missing fields → extraction fails → INTAKE_FAIL",
  "do_not_touch": "existing verifier/fixer/mech-checker paths",
  "acceptance": "make ci-test passes with >= 270 tests"
}
```
""".strip()

PARTIAL_FINALIZED_INTENT_JSON = """
```json
{
  "involved_repos": ["phona/sisyphus"],
  "business_behavior": "partial spec"
}
```
""".strip()


def _get_extractor():
    """Load extract_intake_finalized_intent from wherever it lives."""
    try:
        from orchestrator.router import extract_intake_finalized_intent
        return extract_intake_finalized_intent
    except ImportError:
        pass
    try:
        from orchestrator.webhook import extract_intake_finalized_intent
        return extract_intake_finalized_intent
    except ImportError:
        pass
    # private function fallback
    try:
        from orchestrator.webhook import _extract_intake_finalized_intent
        return _extract_intake_finalized_intent
    except ImportError:
        pytest.skip("extract_intake_finalized_intent not yet implemented")


def test_contract_extract_valid_6_field_json():
    # C13: valid JSON with all 6 required fields → returns dict with those fields
    fn = _get_extractor()
    result = fn(VALID_FINALIZED_INTENT_JSON)
    assert result is not None, "should parse valid 6-field JSON"
    required = {"involved_repos", "business_behavior", "data_constraints",
                "edge_cases", "do_not_touch", "acceptance"}
    assert required.issubset(result.keys()), f"missing fields: {required - result.keys()}"


def test_contract_extract_partial_json_returns_none():
    # C14: JSON with only 2 of 6 required fields → None (schema validation fails)
    fn = _get_extractor()
    result = fn(PARTIAL_FINALIZED_INTENT_JSON)
    assert result is None, "should reject partial JSON missing required fields"


def test_contract_extract_empty_returns_none():
    # C15: empty / no JSON → None
    fn = _get_extractor()
    assert fn("") is None
    assert fn("no json here") is None


# ──────────────────────────────────────────────────────────────────────────────
# C16–C17  Action registry contracts
# ──────────────────────────────────────────────────────────────────────────────

def test_contract_action_start_intake_registered():
    # C16: start_intake action must be registered (INIT→INTAKING uses it)
    from orchestrator.actions import REGISTRY
    assert "start_intake" in REGISTRY, \
        "start_intake not registered; INIT→INTAKING transition will fail at runtime"


def test_contract_action_start_analyze_with_finalized_intent_registered():
    # C17: start_analyze_with_finalized_intent must be registered (INTAKING→ANALYZING uses it)
    from orchestrator.actions import REGISTRY
    assert "start_analyze_with_finalized_intent" in REGISTRY, \
        "start_analyze_with_finalized_intent not registered; INTAKING→ANALYZING will fail at runtime"
