## ADDED Requirements

### Requirement: watchdog SHALL exempt human-in-loop stages from stuck-timeout escalation

The watchdog MUST NOT escalate REQs whose current state belongs to the
human-in-loop set, regardless of how long they have been in that state. The
human-in-loop set SHALL be exposed in code as `orchestrator.watchdog._NO_WATCHDOG_STATES`
and MUST contain `ReqState.INTAKING`. The watchdog MUST include the values of
this set in the `state <> ALL($1::text[])` SQL pre-filter so that exempt rows
are never returned by `req_state` scan and thus never enter the per-row
`_check_and_escalate` logic. No `artifact_checks` row, no `engine.step` call,
and no `req_state.update_context` patch SHALL be produced for an exempt-state
REQ by `watchdog._tick()`.

The exemption MUST hold even when the BKD intake session has ended
(`session_status` in `{"completed", "failed", "cancelled"}`) and the BKD issue
tags do not contain `result:pass` or `result:fail`. The previous
`intake-no-result-tag` detection path is REMOVED by this change.

#### Scenario: WSP-S1 INTAKING + session ended + no result tag → no escalation

- **GIVEN** a REQ in state `INTAKING` whose `updated_at` exceeds both
  `watchdog_session_ended_threshold_sec` and `watchdog_stuck_threshold_sec`
- **AND** the BKD intent issue has `sessionStatus="completed"` and
  `tags=["intake","REQ-x"]` (no `result:pass`, no `result:fail`)
- **WHEN** `watchdog._tick()` runs
- **THEN** the SQL pre-filter excludes the row (it is not in the `rows` list)
- **AND** no `artifact_checks` insert, no `engine.step` call, and no
  `req_state.update_context` patch occur

#### Scenario: WSP-S2 INTAKING + session running → no escalation

- **GIVEN** a REQ in state `INTAKING` past the stuck threshold
- **AND** BKD reports `sessionStatus="running"`
- **WHEN** `watchdog._tick()` runs
- **THEN** the SQL pre-filter excludes the row and no escalation occurs
  (matching the prior running-session skip semantics, but enforced by
  pre-filter rather than per-row check)

#### Scenario: WSP-S3 INTAKING + session ended + result:pass → no escalation

- **GIVEN** a REQ in state `INTAKING` past the stuck threshold
- **AND** BKD reports `sessionStatus="completed"` with `tags` including `result:pass`
- **WHEN** `watchdog._tick()` runs
- **THEN** no escalation occurs (the `INTAKE_PASS` event is the responsibility
  of the BKD webhook router; watchdog SHALL NOT escalate this row even if the
  router missed firing the event)

### Requirement: watchdog SHALL keep escalating non-exempt in-flight stages

For any state NOT in `_NO_WATCHDOG_STATES`, the watchdog MUST keep its existing
stuck-detection and escalate behavior unchanged: SQL pre-filter excludes the row
only when state is in `_SKIP_STATES` (terminal / waiting-for-human / not-yet-in-flow);
otherwise the row enters `_check_and_escalate`, which still respects
`session_status == "running"` skip, the `archive.failed` body.event variant for
ARCHIVING, the `fixer-round-cap` ctx tag for FIXER_RUNNING at cap, and the
generic `body.event="watchdog.stuck"` for everything else.

#### Scenario: WSP-S4 ANALYZING stuck + session=failed → still escalates

- **GIVEN** a REQ in state `ANALYZING` past the threshold with BKD reporting
  `sessionStatus="failed"`
- **WHEN** `watchdog._tick()` runs
- **THEN** the row passes the SQL pre-filter (ANALYZING is not in
  `_NO_WATCHDOG_STATES` nor `_SKIP_STATES`)
- **AND** `engine.step` is called once with `event=SESSION_FAILED`,
  `cur_state=ANALYZING`, and `body.event="watchdog.stuck"`

#### Scenario: WSP-S5 SQL pre-filter args include INTAKING

- **GIVEN** the watchdog's SQL fetch is intercepted
- **WHEN** `watchdog._tick()` runs
- **THEN** the first SQL parameter (the `state <> ALL($1::text[])` array) MUST
  contain the literal string `"intaking"` in addition to the legacy skip
  states `"done"`, `"escalated"`, `"gh-incident-open"`, and `"init"`

### Requirement: escalate action SHALL no longer recognize watchdog.intake_no_result_tag

`actions/escalate.py::_SESSION_END_SIGNALS` MUST NOT contain the string
`"watchdog.intake_no_result_tag"`. Since the watchdog never emits this
`body.event` after this change, the value is dead code; removing it keeps the
session-end signal allowlist tight (each value must correspond to an actually
emitted signal). Other members (`session.failed`, `watchdog.stuck`,
`archive.failed`) MUST remain in place so their respective handlers continue to
fire.

#### Scenario: WSP-S6 _SESSION_END_SIGNALS no longer lists watchdog.intake_no_result_tag

- **GIVEN** the `actions/escalate.py` module is imported
- **WHEN** the test reads `_SESSION_END_SIGNALS`
- **THEN** the set MUST NOT contain `"watchdog.intake_no_result_tag"`
- **AND** the set MUST still contain `"session.failed"`, `"watchdog.stuck"`,
  and `"archive.failed"`
