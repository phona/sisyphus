# REQ-test-router-decision-contract-1777860546 — router verifier decision parsing contract test

Refs phona/sisyphus#371. Complementary to phona/sisyphus#763
(verifier prompt mandates `decision:<action>` tag) and
phona/sisyphus#372 (decode-fail telemetry).

## 现状

`router.derive_verifier_event(description, tags)` is the single funnel where
`session.completed` from a verifier sub-issue gets translated into a state
machine `Event`. The 5/4 v5 dogfood incident (logged on phona/sisyphus#371)
showed the operational cost when this funnel silently picks `VERIFY_ESCALATE`:
the REQ stalls in `ESCALATED`, no error appears in router logs, a human
spends ~30 minutes diagnosing what should have been "verifier said pass".

REQ-fix-verifier-decision-tag-1777812498 (closes phona/sisyphus#356) added
the plain `decision:<action>[-<fixer>]` tag fallback in `verifier_parser`
specifically to handle that incident's input shape. Today the contract is:

| Input shape                                                                | Routing                                              |
|----------------------------------------------------------------------------|------------------------------------------------------|
| tag `decision:<base64-json>` + `verify:<known-stage>` (full schema)        | per-stage pass / `VERIFY_FIX_NEEDED` / `VERIFY_INFRA_RETRY` |
| tag `decision:pass` / `decision:fix-dev` / `decision:fix-spec` / `decision:escalate` / `decision:retry` (plain string) | synthesized low-confidence decision → same routes |
| tag `decision:fail` / any other plain string                               | `VERIFY_ESCALATE` (no synthesis — pinned)            |
| description ` ```json ``` ` block (no tag)                                 | per-stage pass / `VERIFY_FIX_NEEDED`                 |
| valid decision but `verify:<unknown_stage>`                                | `VERIFY_ESCALATE` reason="unknown verifier stage"    |
| valid decision but no `verify:<stage>` tag                                 | `VERIFY_ESCALATE` reason="unknown verifier stage"    |
| base64 that decodes but isn't valid JSON                                   | `VERIFY_ESCALATE` reason="no decision JSON"          |
| valid JSON that fails schema (e.g. missing `confidence`)                   | `VERIFY_ESCALATE` reason="invalid decision: …"       |
| nothing parseable anywhere                                                 | `VERIFY_ESCALATE` reason="no decision JSON"          |

This contract is currently tested only end-to-end (engine + webhook tests) and
inside the parser's own unit tests. Drift in any of the cells above is
discoverable only when a live REQ silently sticks.

## 提议

Add a single contract test file —
`orchestrator/tests/router/test_verifier_decision_parsing.py` — that:

1. Parametrises the table above against `router.derive_verifier_event`.
2. Asserts both the returned `Event` and a substring of the diagnostic
   `reason` (so the routing decision and the reason it was made are both
   pinned).
3. Adds a small number of focused single-purpose tests for invariants that
   don't fit a parametrised case cleanly (precedence of base64 tag over
   plain tag, defensive None/empty inputs).

Scope is intentionally narrow: only `orchestrator/tests/router/` is added.
`router.py`, `verifier_parser.py`, prompts and `state.py` are not modified.
If running the contract test reveals a divergence from the expected routes,
that is the signal phona/sisyphus#371 was after — the failure tells us
exactly which row of the table changed, and the fix lands in a follow-up
issue, not in this REQ.

## Why a new spec capability

The closest existing capability is `verifier-decision-tag-fallback`
(REQ-fix-verifier-decision-tag-1777812498), which specifies the parser-layer
synthesis. The router-layer contract (decision dict → state machine event,
including unknown-stage escalate, schema-invalid escalate, no-JSON escalate)
is currently un-spec'd. Introducing `router-decision-contract-tests` keeps
the parser contract and the router contract independently archivable; future
parser additions (new aliases) and router changes (new `verify:<stage>`
routes) won't have to share a single growing spec file.

## 关联

- closes phona/sisyphus#371
- 互补 phona/sisyphus#763 (verifier prompt mandates `decision:<action>` tag)
- 互补 phona/sisyphus#372 (decode-fail telemetry)
- builds on REQ-fix-verifier-decision-tag-1777812498
  (capability `verifier-decision-tag-fallback`, parser synthesis layer)
