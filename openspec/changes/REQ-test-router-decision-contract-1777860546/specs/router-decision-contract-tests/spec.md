# Spec delta — router-decision-contract-tests

## ADDED Requirements

### Requirement: router SHALL pin the verifier decision → state-machine Event contract via a parametrised contract test

The orchestrator MUST ship a parametrised contract test (located at
`orchestrator/tests/router/test_verifier_decision_parsing.py`) that exercises
`orchestrator.router.derive_verifier_event(description, tags)` against every
input shape currently produced by verifier-agents in production, and SHALL
assert both the returned `Event` and a stable substring of the diagnostic
`reason`. The test SHALL cover, at minimum: a tag-base64 happy path that
routes to a stage-specific pass event, a tag-base64 fix decision that routes
to `VERIFY_FIX_NEEDED`, a plain `decision:<action>` tag fallback path, a
description-only ` ```json ``` ` path, every silent-escalate case (unknown
verifier stage, schema-invalid JSON, base64 that decodes to invalid JSON,
plain `decision:fail` tag, empty inputs), and a precedence test asserting
that a base64 decision tag wins over a plain `decision:<action>` tag. The
test MUST NOT modify `router.py`, `verifier_parser.py`, prompts, or
`state.py`; if executing the test reveals a routing divergence, that is the
intended regression signal — the fix lands in a follow-up issue, not in this
test file.

#### Scenario: RDCT-S1 contract test runs in isolation under pytest
- **GIVEN** the orchestrator package is installed and pytest is available
- **WHEN** an operator runs
  `pytest orchestrator/tests/router/test_verifier_decision_parsing.py -v`
- **THEN** every parametrised case and every focused invariant test passes
- **AND** the run does not depend on a database, network, BKD, or Kubernetes

#### Scenario: RDCT-S2 happy path tag-base64 routes to per-stage pass event
- **GIVEN** `tags = ["decision:<base64-of-valid-pass>", "verify:dev_cross_check"]`
  where the JSON decodes to a schema-valid pass decision
- **WHEN** `derive_verifier_event(None, tags)` is called
- **THEN** the returned `Event` is `Event.DEV_CROSS_CHECK_PASS`
- **AND** the returned reason is the empty string

#### Scenario: RDCT-S3 plain `decision:pass` tag routes via parser fallback
- **GIVEN** `tags = ["decision:pass", "verify:dev_cross_check"]` (the exact
  shape that triggered the 5/4 v5 dogfood incident; phona/sisyphus#371)
- **WHEN** `derive_verifier_event(None, tags)` is called
- **THEN** the returned `Event` is `Event.DEV_CROSS_CHECK_PASS`
- **AND** the contract test pins that this is now a happy path post
  REQ-fix-verifier-decision-tag-1777812498 (closes phona/sisyphus#356),
  not `VERIFY_ESCALATE`

#### Scenario: RDCT-S4 plain `decision:fail` tag still escalates (no synthesis)
- **GIVEN** `tags = ["decision:fail", "verify:dev_cross_check"]`
- **WHEN** `derive_verifier_event(None, tags)` is called
- **THEN** the returned `Event` is `Event.VERIFY_ESCALATE`
- **AND** the returned `reason` contains the substring `no decision JSON`,
  since `decision:fail` is intentionally absent from the parser's plain-tag
  alias table

#### Scenario: RDCT-S5 schema-invalid base64 tag escalates with `invalid decision` reason
- **GIVEN** `tags = ["decision:eyJhY3Rpb24iOiJwYXNzIn0=", "verify:dev_cross_check"]`
  (base64 of `{"action":"pass"}`, which is missing the required `confidence` field)
- **WHEN** `derive_verifier_event(None, tags)` is called
- **THEN** the returned `Event` is `Event.VERIFY_ESCALATE`
- **AND** the returned `reason` contains the substring `invalid decision`

#### Scenario: RDCT-S6 unknown verifier stage with valid pass decision escalates
- **GIVEN** a schema-valid pass decision in a base64 tag, but the only
  `verify:<stage>` tag is `verify:unknown_stage` (a stage not in the
  router's `_VERIFY_PASS_ROUTING` table)
- **WHEN** `derive_verifier_event(None, tags)` is called
- **THEN** the returned `Event` is `Event.VERIFY_ESCALATE`
- **AND** the returned `reason` contains the substring `unknown verifier stage`

#### Scenario: RDCT-S7 base64 tag wins over plain tag at the same precedence boundary
- **GIVEN** `tags` contains both a base64 tag carrying a `fix` decision and
  a plain `decision:pass` tag, plus `verify:dev_cross_check`
- **WHEN** `derive_verifier_event(None, tags)` is called
- **THEN** the returned `Event` is `Event.VERIFY_FIX_NEEDED` (the base64
  decision wins)
- **AND** the returned decision dict carries the base64 payload's `fixer`
  value, not the plain tag's synthesized `null`
