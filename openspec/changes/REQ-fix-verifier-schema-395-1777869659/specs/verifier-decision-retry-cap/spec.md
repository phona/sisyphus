# Spec delta — verifier-decision-retry-cap

## ADDED Requirements

### Requirement: webhook retry cap for verifier decision parse failure SHALL be 3

The orchestrator webhook handler SHALL automatically follow-up the verifier-agent
with a corrective prompt up to **3 times** when its `session.completed` payload
yields a parseable-but-schema-invalid decision (i.e. `extract_decision_robust`
returned `retry_worthy=true`). The retry counter MUST be persisted on
`req_state.context.verifier_parse_retry_count`. On the 4th failed parse the
handler MUST stop retrying and let the existing `VERIFY_ESCALATE` path fire.
The retry follow-up prompt (`webhook._VERIFIER_RETRY_PROMPT`) MUST contain all
three mandate phrases the prompt suite already requires of agents:
the JSON block goes in the **last assistant message**, a redundant
`decision:<action>[-<fixer>]` BKD tag MUST be PATCHed first, and the `action`
field MUST be one of the four literals `pass` / `fix` / `escalate` / `retry`.

#### Scenario: VDRC-S1 third schema-invalid attempt still triggers a follow-up retry
- **GIVEN** a verifier sub-issue with `req_state.context.verifier_parse_retry_count = 2`
- **AND** the new `session.completed` payload still yields `retry_worthy=true`
- **WHEN** the webhook handler processes the event
- **THEN** the handler issues exactly one BKD `follow_up_issue` call with
  `webhook._VERIFIER_RETRY_PROMPT` as the prompt
- **AND** the handler returns `{"action": "skip", "reason": "verifier_parse_retry_3"}`
- **AND** `req_state.context.verifier_parse_retry_count` is updated to `3`

#### Scenario: VDRC-S2 fourth schema-invalid attempt escalates without follow-up
- **GIVEN** a verifier sub-issue with `req_state.context.verifier_parse_retry_count = 3`
- **AND** the new `session.completed` payload still yields `retry_worthy=true`
- **WHEN** the webhook handler processes the event
- **THEN** no `follow_up_issue` call is made
- **AND** the derived event is `Event.VERIFY_ESCALATE`
- **AND** `req_state.context.escalated_reason` is set to `verifier-decision`

#### Scenario: VDRC-S3 retry follow-up prompt mandates the three rules
- **GIVEN** the constant `webhook._VERIFIER_RETRY_PROMPT`
- **WHEN** an operator inspects its text
- **THEN** the text contains the substring `last assistant message`
  (case-insensitive)
- **AND** the text contains the substring `decision:` (the BKD tag mandate)
- **AND** the text lists the four valid action literals `pass`, `fix`,
  `escalate`, `retry` together with the JSON skeleton

### Requirement: prompt-suite lint script SHALL guard verifier prompts at CI time

The repository SHALL ship a static linter at `scripts/lint-verifier-prompts.py`
that exits non-zero when the verifier prompt suite drifts from the contract
this REQ pins. The linter MUST be runnable with stdlib python3 only (no third
party deps), so it can run in any CI environment without `uv`/`pip install`.
It MUST scan `orchestrator/src/orchestrator/prompts/verifier/` and enforce:

1. `_decision.md.j2` MUST contain each of the four valid action literals
   `"pass"`, `"fix"`, `"escalate"`, `"retry"` (with surrounding double quotes,
   so JSON examples are matched, not prose).
2. `_decision.md.j2` MUST contain at least one occurrence of the literal
   phrase `HARD CONSTRAINT`, the literal substring `decision:` (the BKD tag
   mandate), and the literal substring `最后一条 assistant message`
   (the last-assistant-message rule).
3. Every per-stage verifier prompt matching the glob
   `<stage>_<trigger>.md.j2` (i.e. files **not** starting with `_`) MUST
   include `_decision.md.j2` via a Jinja `{% include "verifier/_decision.md.j2" %}`
   directive. Files starting with `_` (partials) are not subject to this rule.

The CI workflow `.github/workflows/orchestrator-ci.yml` MUST invoke this
linter in the `lint-test` job, so that any verifier-prompt drift fails the
PR check. The script MUST print a one-line per-violation report to stdout
before exiting non-zero, so the CI log makes the diagnosis obvious.

#### Scenario: VDRC-S4 lint passes on the in-tree prompt suite
- **GIVEN** the unmodified `orchestrator/src/orchestrator/prompts/verifier/`
  directory at HEAD
- **WHEN** an operator runs `python3 scripts/lint-verifier-prompts.py`
- **THEN** the exit code is `0`
- **AND** stdout contains a final `OK` line summarizing the count of
  prompts checked

#### Scenario: VDRC-S5 lint flags a stage prompt missing the `_decision.md.j2` include
- **GIVEN** a per-stage verifier prompt (e.g. `analyze_fail.md.j2`) where the
  `{% include "verifier/_decision.md.j2" %}` line has been removed
- **WHEN** an operator runs `python3 scripts/lint-verifier-prompts.py`
- **THEN** the exit code is non-zero
- **AND** stdout contains a line that names the offending file and the
  reason `missing decision include`

#### Scenario: VDRC-S6 lint flags `_decision.md.j2` missing a mandate phrase
- **GIVEN** `_decision.md.j2` from which the literal phrase
  `HARD CONSTRAINT` has been deleted
- **WHEN** an operator runs `python3 scripts/lint-verifier-prompts.py`
- **THEN** the exit code is non-zero
- **AND** stdout contains a line naming `_decision.md.j2` and the reason
  `missing mandate phrase: HARD CONSTRAINT`
