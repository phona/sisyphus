## ADDED Requirements

### Requirement: force_escalate closes in-flight stage_run before state change

When `POST /admin/req/{req_id}/escalate` is called on a REQ whose current state
maps to a running stage (via `STATE_TO_STAGE`), the system SHALL call
`close_latest_stage_run` with `outcome='escalated'` and
`fail_reason='admin-force-escalate'` **before** performing the raw SQL state update.
This MUST happen to prevent `ended_at IS NULL` long-tail rows from accumulating in
`stage_runs` and polluting `stage_stats` metrics. Failure to close MUST only emit a
warning log, not abort the escalate operation.

When the current state has no corresponding stage in `STATE_TO_STAGE` (e.g. INIT,
DONE, ESCALATED), the system SHALL skip the close call and proceed directly to the
raw SQL UPDATE.

#### Scenario: FESC-S1 ANALYZING state closes analyze stage_run before escalate

- **GIVEN** a REQ in state ANALYZING with an open stage_run (ended_at IS NULL)
- **WHEN** `POST /admin/req/{req_id}/escalate` is called
- **THEN** `close_latest_stage_run(req_id, "analyze", outcome="escalated", fail_reason="admin-force-escalate")` is called
- **AND** the call happens before the raw SQL UPDATE to req_state

#### Scenario: FESC-S2 INIT state skips stage_run close

- **GIVEN** a REQ in state INIT (no corresponding entry in STATE_TO_STAGE)
- **WHEN** `POST /admin/req/{req_id}/escalate` is called
- **THEN** `close_latest_stage_run` is NOT called
- **AND** the REQ state is set to escalated successfully
