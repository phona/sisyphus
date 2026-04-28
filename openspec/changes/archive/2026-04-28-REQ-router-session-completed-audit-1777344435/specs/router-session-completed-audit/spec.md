## ADDED Requirements

### Requirement: webhook pre-sets escalated_reason for all session.completed escalate paths

The system SHALL pre-set `ctx.escalated_reason` before invoking the escalate action for
every `session.completed` event that routes to the escalate action. The pre-set reason MUST
be more specific than the generic fallback `"session-completed"`. Specifically, the system
MUST map `VERIFY_ESCALATE` to `"verifier-decision"`, `INTAKE_FAIL` to `"intake-fail"`, and
`PR_CI_TIMEOUT` to `"pr-ci-timeout"`.

#### Scenario: RSCA-S1 INTAKE_FAIL from session.completed gets reason=intake-fail

- **GIVEN** an intake-agent session.completed arrives with tags `["intake", "REQ-x", "result:fail"]`
- **WHEN** derive_event returns INTAKE_FAIL and webhook step 5.8 runs
- **THEN** ctx.escalated_reason is set to "intake-fail"
- **AND** when escalate action fires, final_reason is "intake-fail" (not "session-completed")

#### Scenario: RSCA-S2 PR_CI_TIMEOUT from session.completed gets reason=pr-ci-timeout

- **GIVEN** a pr-ci-watch session.completed arrives with tags `["pr-ci", "REQ-x", "pr-ci:timeout"]`
- **WHEN** derive_event returns PR_CI_TIMEOUT and webhook step 5.8 runs
- **THEN** ctx.escalated_reason is set to "pr-ci-timeout"
- **AND** when escalate action fires, final_reason is "pr-ci-timeout" (not "session-completed")

#### Scenario: RSCA-S3 VERIFY_ESCALATE retains reason=verifier-decision

- **GIVEN** a verifier session.completed arrives with a valid decision JSON action=escalate
- **WHEN** derive_verifier_event returns VERIFY_ESCALATE and webhook step 5.8 runs
- **THEN** ctx.escalated_reason remains "verifier-decision"

### Requirement: derive_event returns None for session.completed without recognized result

The system SHALL return `None` (silently skip) from `derive_event` when a
`session.completed` event carries a known stage tag but no recognized result tag. This MUST
NOT produce a `SESSION_FAILED` event. Known stage tags that require a result tag are:
`challenger`, `staging-test`, `accept`, `pr-ci`, and `intake`. Tags that never require a
result tag are: `fixer`, `analyze`, `done-archive`.

#### Scenario: RSCA-S4 challenger without result returns None

- **GIVEN** a session.completed event with tags `["challenger", "REQ-x"]`
- **WHEN** derive_event is called
- **THEN** result is None (not SESSION_FAILED, not CHALLENGER_FAIL)

#### Scenario: RSCA-S5 session.completed with no stage tag returns None

- **GIVEN** a session.completed event with tags `["REQ-x"]` (no stage tag)
- **WHEN** derive_event is called
- **THEN** result is None

#### Scenario: RSCA-S6 session.completed with known stage and unrecognized result returns None

- **GIVEN** a session.completed event with tags `["challenger", "REQ-x", "result:weird"]`
- **WHEN** derive_event is called
- **THEN** result is None (unknown result variant is silently skipped)

#### Scenario: RSCA-S7 fixer without extra tags returns FIXER_DONE

- **GIVEN** a session.completed event with tags `["fixer", "REQ-x"]` (no fixer:dev or result tag)
- **WHEN** derive_event is called
- **THEN** result is FIXER_DONE (fixer never requires a result tag)
