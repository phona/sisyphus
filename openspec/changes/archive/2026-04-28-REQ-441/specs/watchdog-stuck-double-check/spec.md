## ADDED Requirements

### Requirement: watchdog SHALL reconcile BKD sessionStatus for CHALLENGER_RUNNING before escalating

The watchdog MUST track the BKD issue associated with `CHALLENGER_RUNNING` state
via `orchestrator.watchdog._STATE_ISSUE_KEY`. The entry for
`ReqState.CHALLENGER_RUNNING` MUST map to the context key `"challenger_issue_id"`.
Before deciding that a `CHALLENGER_RUNNING` REQ is stuck, the watchdog MUST call
`bkd.get_issue()` to retrieve the live `session_status`. If `session_status` is
`"running"`, the watchdog MUST skip escalation for that REQ (same long-tail
protection semantics as other `autonomous-bounded` stages). Only when
`session_status` is not `"running"` (ended / failed / cancelled / issue not found)
SHALL the watchdog apply the `ended_sec` threshold and potentially escalate.

#### Scenario: WSD-S1 CHALLENGER_RUNNING + session running → no escalation

- **GIVEN** a REQ in state `CHALLENGER_RUNNING` whose `updated_at` exceeds
  `watchdog_session_ended_threshold_sec` (300 s)
- **AND** `ctx["challenger_issue_id"]` is set to a valid BKD issue ID
- **AND** BKD reports `session_status="running"` for that issue
- **WHEN** `watchdog._tick()` runs
- **THEN** no `artifact_checks` insert, no `engine.step` call occur for this REQ
- **AND** the result `escalated` count is 0 for this row

#### Scenario: WSD-S2 CHALLENGER_RUNNING + session failed → escalate after ended_sec

- **GIVEN** a REQ in state `CHALLENGER_RUNNING` whose `updated_at` exceeds
  `watchdog_session_ended_threshold_sec` (300 s)
- **AND** `ctx["challenger_issue_id"]` is set to a valid BKD issue ID
- **AND** BKD reports `session_status="failed"` for that issue
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` is called once with `event=SESSION_FAILED`,
  `cur_state=CHALLENGER_RUNNING`, and `body.issueId=challenger_issue_id`
- **AND** the result `escalated` count is 1

#### Scenario: WSD-S3 CHALLENGER_RUNNING entry present in _STATE_ISSUE_KEY

- **GIVEN** the `orchestrator.watchdog` module is imported
- **WHEN** `_STATE_ISSUE_KEY[ReqState.CHALLENGER_RUNNING]` is read
- **THEN** the value MUST equal `"challenger_issue_id"`
