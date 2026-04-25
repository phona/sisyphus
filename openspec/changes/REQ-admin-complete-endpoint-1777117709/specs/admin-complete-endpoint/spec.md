# admin-complete-endpoint

## ADDED Requirements

### Requirement: /admin/req/{req_id}/complete moves a stale escalated REQ to DONE and triggers immediate runner cleanup

The orchestrator SHALL expose a `POST /admin/req/{req_id}/complete` HTTP
endpoint behind the same Bearer-token auth as the other `/admin/*` routes
(`webhook._verify_token`). The endpoint MUST move the named REQ from state
`escalated` to state `done` via a direct SQL UPDATE on `req_state` (NOT
through `engine.step` / the state-machine transition table — admin
overrides do not pollute the legal transition set), and MUST schedule
runner cleanup with `retain_pvc=False` so the PVC is released immediately
rather than waiting for `runner_gc` to scan on its next interval. The
endpoint SHALL append a history entry capturing the override
(`{"event": "admin.complete", "from": "escalated", "to": "done"}`) so
`req_summary` and audit queries can attribute the state change to the
admin action.

#### Scenario: ACE-S1 happy path: escalated REQ becomes done, cleanup task scheduled

- **GIVEN** a REQ row with `state='escalated'` exists in `req_state` and
  the K8s runner controller is initialized
- **WHEN** the client sends `POST /admin/req/REQ-X/complete` with a valid
  Bearer token and an empty body
- **THEN** the endpoint MUST execute a SQL UPDATE setting `state='done'`
  on the row keyed by `req_id='REQ-X'`
- **AND** the response MUST be 200 with JSON body containing
  `action == "completed"` and `from_state == "escalated"`
- **AND** an `asyncio.Task` running `_cleanup_runner_on_terminal(req_id, ReqState.DONE)`
  MUST be scheduled (fire-and-forget) before the endpoint returns
- **AND** the SQL UPDATE MUST also append an entry to the `history` JSON
  array containing the keys `from='escalated'`, `to='done'`,
  `event='admin.complete'`

### Requirement: /admin/req/{req_id}/complete is idempotent on already-done REQs

The endpoint MUST return HTTP 200 with `action == "noop"` when invoked on
a REQ already in state `done`. The handler MUST NOT execute any SQL
UPDATE in this case (no spurious history entry, no spurious cleanup task)
because a second cleanup is wasteful and a second history entry would
corrupt the audit trail.

#### Scenario: ACE-S2 second call on already-done REQ is a 200 noop

- **GIVEN** a REQ row with `state='done'` exists
- **WHEN** the client sends `POST /admin/req/REQ-X/complete`
- **THEN** the response MUST be 200 with body containing
  `action == "noop"` and `state == "already done"`
- **AND** no SQL UPDATE on `req_state` MUST be executed
- **AND** no cleanup task MUST be scheduled

### Requirement: /admin/req/{req_id}/complete refuses non-ESCALATED states with HTTP 409

The endpoint MUST return HTTP 409 Conflict when the REQ is in any state
other than `escalated` or `done`. The error body MUST include the current
state name and a hint pointing the operator at `/admin/req/{req_id}/escalate`
as the prerequisite for moving an in-flight REQ toward terminal cleanup.
This intentionally narrow precondition prevents an admin from
short-circuiting an in-flight stage (e.g. ANALYZING with a running
runner Pod) into DONE in a single call, which would race the action
handler still touching the workspace and silently drop in-progress work.

#### Scenario: ACE-S3 calling complete on an in-flight REQ returns 409

- **GIVEN** a REQ row with `state='analyzing'` exists
- **WHEN** the client sends `POST /admin/req/REQ-X/complete`
- **THEN** the response MUST be 409 with detail containing the literal
  substring `analyzing` and a hint mentioning `/admin/req/.../escalate`
- **AND** no SQL UPDATE on `req_state` MUST be executed
- **AND** no cleanup task MUST be scheduled

### Requirement: /admin/req/{req_id}/complete returns 404 when the REQ is unknown

The endpoint MUST return HTTP 404 Not Found when no row matches the
`req_id` path parameter, with the same error shape as the other
`/admin/req/{req_id}/*` endpoints (`{"detail": "req <id> not found"}`).
This MUST happen before any state-precondition check so that "no such
REQ" cannot be confused with "REQ in wrong state".

#### Scenario: ACE-S4 unknown REQ returns 404

- **GIVEN** no row in `req_state` with `req_id='REQ-DOES-NOT-EXIST'`
- **WHEN** the client sends `POST /admin/req/REQ-DOES-NOT-EXIST/complete`
- **THEN** the response MUST be 404 with detail containing the literal
  substring `not found`

### Requirement: /admin/req/{req_id}/complete optionally accepts a reason in body and persists it in context

The endpoint SHALL accept an optional JSON body `{"reason": "<string>"}`.
When provided, the `reason` MUST be persisted in `req_state.context`
under the key `completed_reason_detail` so audit queries can recover the
operator's intent. When omitted, the context MUST still record
`completed_reason='admin'` (a fixed marker indicating the source of the
override). The body itself remains optional — POST with no body or
`{}` MUST succeed identically to the no-body path.

#### Scenario: ACE-S5 reason in body is persisted to context

- **GIVEN** a REQ row with `state='escalated'` exists
- **WHEN** the client sends `POST /admin/req/REQ-X/complete` with body
  `{"reason": "superseded by REQ-Y"}`
- **THEN** the SQL UPDATE MUST patch `context` so that
  `context.completed_reason == 'admin'` AND
  `context.completed_reason_detail == 'superseded by REQ-Y'`

#### Scenario: ACE-S6 missing body still works with default reason marker

- **GIVEN** a REQ row with `state='escalated'` exists
- **WHEN** the client sends `POST /admin/req/REQ-X/complete` with empty body
- **THEN** the response MUST be 200 with `action == "completed"`
- **AND** the SQL UPDATE MUST patch `context.completed_reason = 'admin'`
- **AND** `context` MUST NOT have a `completed_reason_detail` key (no
  spurious null-string field)

### Requirement: /admin/req/{req_id}/complete enforces the same Bearer auth as other admin endpoints

The endpoint MUST call `webhook._verify_token(authorization)` as the
first action before reading any state, mirroring `force_escalate`,
`emit_event`, and the v0.2 runner-ops endpoints. A missing or invalid
token MUST yield HTTP 401 / 403 (whichever `_verify_token` raises) and
MUST NOT execute any SQL UPDATE or cleanup task.

#### Scenario: ACE-S7 missing Bearer token rejects before any state read

- **GIVEN** any state of any REQ
- **WHEN** the client sends `POST /admin/req/REQ-X/complete` without an
  `Authorization` header
- **THEN** `_verify_token` MUST be invoked and MUST raise the same
  HTTPException as the other admin endpoints
- **AND** `req_state.get` MUST NOT be called
- **AND** no SQL UPDATE on `req_state` MUST be executed
