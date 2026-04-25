## ADDED Requirements

### Requirement: watchdog identifies intake session completed without result tag

The watchdog MUST detect, as a distinct stuck-stage class, the case where a REQ is
in state `INTAKING` for longer than `watchdog_stuck_threshold_sec`, the BKD intake
issue's `session_status` is not `"running"` (i.e. the agent session has ended), and
the BKD issue's `tags` contain neither `result:pass` nor `result:fail`. When this
specific condition holds, the watchdog SHALL escalate the REQ with
`reason="intake-no-result-tag"` rather than the generic `reason="watchdog_stuck"`.

For all other stuck states (including `INTAKING` cases where a `result:*` tag is
present but the state still did not transition, or where the BKD issue lookup
failed), the watchdog MUST fall through to the existing generic
`reason="watchdog_stuck"` escalation path. The intake detection MUST be guarded
against `issue is None` (BKD lookup failure or stage not mapped to an issue), so
infrastructure flakiness does not produce false-positive `intake-no-result-tag`
verdicts.

#### Scenario: WD-S1 INTAKING + session ended + no result tag → intake-no-result-tag

- **GIVEN** a REQ in state `INTAKING` whose `updated_at` exceeds the stuck threshold
- **AND** the BKD intent issue has `sessionStatus="completed"` and
  `tags=["intake","REQ-x"]` (no `result:pass`, no `result:fail`)
- **WHEN** the watchdog tick processes this row
- **THEN** an `artifact_checks` row is written with
  `stage="watchdog:intake-no-result-tag"` and `reason="intake-no-result-tag"`
- **AND** `req_state.update_context` patches `escalated_reason="intake-no-result-tag"`
- **AND** `engine.step` is invoked with `event=SESSION_FAILED` and
  `body.event="watchdog.intake_no_result_tag"`

#### Scenario: WD-S2 INTAKING + session running → skip

- **GIVEN** a REQ in state `INTAKING` past the stuck threshold
- **AND** BKD reports `sessionStatus="running"`
- **WHEN** the watchdog tick processes this row
- **THEN** no escalation occurs, no artifact row is written, and `engine.step`
  is not invoked

#### Scenario: WD-S3 INTAKING + session completed + result:pass → generic stuck path

- **GIVEN** a REQ in state `INTAKING` past the stuck threshold
- **AND** BKD reports `sessionStatus="completed"` with `tags` including `result:pass`
- **WHEN** the watchdog tick processes this row
- **THEN** the artifact row uses generic `stage="watchdog:intaking"` and
  `reason="watchdog_stuck"`
- **AND** `body.event="watchdog.stuck"` (not the intake-specific event)
- **AND** `escalated_reason` is NOT pre-written to ctx by the watchdog

#### Scenario: WD-S4 BKD lookup failure → generic stuck path

- **GIVEN** a REQ in state `INTAKING` past the stuck threshold
- **AND** the BKD `get_issue` call raises (network / 5xx / issue deleted)
- **WHEN** the watchdog tick processes this row
- **THEN** the watchdog escalates via the generic `reason="watchdog_stuck"` path
- **AND** does NOT misclassify the row as `intake-no-result-tag`

### Requirement: escalate action treats watchdog.intake_no_result_tag as session-end + non-transient

The `escalate` action MUST treat `body.event="watchdog.intake_no_result_tag"` as a
session-end signal: the action SHALL execute the manual CAS push to `ESCALATED`
state and the runner cleanup branch (the same branch that runs for `session.failed`
and `watchdog.stuck`).

The `escalate` action MUST NOT treat `watchdog.intake_no_result_tag` as a transient
signal: it SHALL skip the auto-resume `follow_up_issue` "continue" path and proceed
directly to real escalation, since the intake session has already completed and
auto-resume cannot revive a finished agent.

The reason recorded on the BKD intent issue (the `reason:<value>` tag added
alongside `escalated`) SHALL be `intake-no-result-tag`, sourced from
`ctx.escalated_reason` (which the watchdog pre-writes), since
`watchdog.intake_no_result_tag` is intentionally outside `_CANONICAL_SIGNALS` to
allow the ctx value to win.

#### Scenario: WD-S5 escalate honors ctx.escalated_reason for intake event

- **GIVEN** `body.event="watchdog.intake_no_result_tag"`
- **AND** `ctx.escalated_reason="intake-no-result-tag"`
- **WHEN** the escalate action runs
- **THEN** the BKD `merge_tags_and_update` call adds `escalated` and
  `reason:intake-no-result-tag` to the intent issue
- **AND** `follow_up_issue` is NOT awaited (no auto-resume)

#### Scenario: WD-S6 escalate runs cleanup CAS for intake event

- **GIVEN** `body.event="watchdog.intake_no_result_tag"` and the REQ's current state
  is `INTAKING` (not yet `ESCALATED`)
- **WHEN** the escalate action runs
- **THEN** `req_state.cas_transition` is awaited to push the REQ to `ESCALATED`
- **AND** the runner cleanup is awaited (`cleanup_runner` called once)
