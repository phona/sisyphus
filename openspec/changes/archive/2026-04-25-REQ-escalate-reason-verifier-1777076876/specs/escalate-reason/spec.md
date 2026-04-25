## ADDED Requirements

### Requirement: webhook sets escalated_reason when verifier decides to escalate

When a verifier agent session completes with `action=escalate`, the webhook handler MUST
set `ctx.escalated_reason = "verifier-decision"` before calling `engine.step`. The `escalate`
action SHALL use this value as the termination reason, ensuring downstream log entries and
BKD tags carry a meaningful reason string rather than falling back to `"session-completed"`.

#### Scenario: ERV-S1 verifier escalate sets reason verifier-decision in context

- **GIVEN** a verifier agent session.completed with action=escalate decision
- **WHEN** webhook processes the event and derives VERIFY_ESCALATE
- **THEN** ctx.escalated_reason is set to "verifier-decision" before engine.step is called
- **AND** the escalate action returns reason="verifier-decision"

#### Scenario: ERV-S2 verifier-decision is not treated as transient

- **GIVEN** ctx.escalated_reason = "verifier-decision"
- **WHEN** escalate action evaluates _is_transient
- **THEN** _is_transient returns False
- **AND** the action performs real escalate without auto-resume follow-up
