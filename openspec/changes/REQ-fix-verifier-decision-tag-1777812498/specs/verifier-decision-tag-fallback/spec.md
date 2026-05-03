# Spec delta — verifier-decision-tag-fallback

## ADDED Requirements

### Requirement: verifier-agent SHALL emit a plain `decision:<action>` BKD tag before completing

The verifier-agent prompt (`_decision.md.j2`) MUST instruct the agent to
`PATCH` its own BKD issue with a plain `decision:<action>[-<fixer>]` tag prior
to writing the JSON decision block. The tag value SHALL match the exact JSON
`action`, with `-dev` / `-spec` suffix when `action="fix"`. The instruction
SHALL be presented as a hard requirement (HARD CONSTRAINT level), with a
`curl` example identical to other tags-mutation examples in the prompt suite.
The tag SHALL be additive — the agent MUST first GET the current tags and
preserve them per the existing tag-merge convention.

#### Scenario: VDTF-S1 prompt rendered for any verifier stage contains the mandatory tag instruction
- **GIVEN** the verifier prompt is rendered (any stage / trigger combination)
- **WHEN** an operator inspects the rendered text
- **THEN** the rendered prompt contains a section that mandates emitting a
  `decision:<action>[-<fixer>]` BKD tag, with at least one literal example such
  as `decision:pass`, `decision:fix-dev`, `decision:fix-spec`, `decision:escalate`, or `decision:retry`
- **AND** the same section provides a `curl PATCH` example showing the
  tag-merge pattern

### Requirement: parser SHALL synthesize a low-confidence decision from the plain tag when JSON extraction fails

The orchestrator's `verifier_parser.extract_decision_robust` SHALL, after the
existing base64-tag and text-embedded-JSON paths have both failed to produce a
parseable decision, scan the input `tags` for a plain `decision:<action>` (or
`decision:<action>-<fixer>`) marker. When such a marker is present and `action`
is one of `pass` / `fix` / `escalate` / `retry`, the parser MUST synthesize a
minimal decision dict equivalent to the one a well-behaved agent would have
written. The synthesized dict MUST set `confidence="low"` and `reason` to a
string starting with the literal `orch-fallback`, so dashboards can surface
inferred routes. The synthesized dict MUST validate against
`router.validate_decision` (i.e. `fixer` is `null` for `pass`/`escalate`/`retry`,
and `dev`/`spec` for `fix`). If `action="fix"` is present without a `-dev` /
`-spec` suffix, the parser MUST NOT synthesize a decision (no defensible
default), allowing the existing escalate path to fire.

#### Scenario: VDTF-S2 plain `decision:pass` tag with no JSON in text yields a synthesized pass decision
- **GIVEN** an input where `tags = ["verifier", "verify:staging_test", "decision:pass"]`
  and `description` text contains no JSON code block at all
- **WHEN** `extract_decision_robust(description, tags)` is called
- **THEN** the returned `ParseResult.decision` is a dict with `action="pass"`,
  `fixer` equal to `null`, `confidence="low"`, and `reason` starting with
  `orch-fallback`
- **AND** the result validates as ok via `router.validate_decision`

#### Scenario: VDTF-S3 plain `decision:fix` without fixer suffix is not synthesized
- **GIVEN** an input where `tags = ["verifier", "decision:fix"]` (no `-dev` /
  `-spec` suffix) and the text contains no JSON
- **WHEN** `extract_decision_robust(description, tags)` is called
- **THEN** `ParseResult.decision` is `None` (parser declines to guess the
  fixer), preserving the existing `VERIFY_ESCALATE` route
- **AND** the result validates as **not** ok via `router.validate_decision`
  (because no decision was synthesized at all)
