# admin-resume-endpoint

## ADDED Requirements

### Requirement: /admin/req/{req_id}/resume dispatches VERIFY_PASS or VERIFY_FIX_NEEDED to unblock an escalated REQ

The orchestrator SHALL expose a `POST /admin/req/{req_id}/resume` HTTP
endpoint behind the same Bearer-token auth as the other `/admin/*` routes
(`webhook._verify_token`). The endpoint MUST accept a JSON body with a
required `action` field of value `"pass"` or `"fix-needed"`, and SHALL
dispatch the corresponding state-machine event (`Event.VERIFY_PASS` or
`Event.VERIFY_FIX_NEEDED`) through `engine.step` so that the existing
`(ESCALATED, VERIFY_PASS) → REVIEW_RUNNING` and `(ESCALATED, VERIFY_FIX_NEEDED)
→ FIXER_RUNNING` transitions execute their action handlers
(`apply_verify_pass` / `start_fixer`). The endpoint MUST NOT introduce new
events or transitions; it is purely an injection entry-point that mirrors
the BKD verifier-agent follow-up path without requiring a verifier session.

#### Scenario: ARE-S1 happy path: action=pass dispatches VERIFY_PASS

- **GIVEN** a REQ row with `state='escalated'` and
  `context.verifier_stage='staging_test'`
- **WHEN** the client sends `POST /admin/req/REQ-X/resume` with body
  `{"action": "pass"}` and a valid Bearer token
- **THEN** the endpoint MUST call `engine.step` exactly once with
  `event=Event.VERIFY_PASS` and `cur_state=ReqState.ESCALATED`
- **AND** the response MUST be 200 with body containing
  `action == "resumed"` and `event == "verify.pass"` and
  `from_state == "escalated"`
- **AND** prior to the dispatch the endpoint MUST patch
  `req_state.context` to set `resumed_by_admin = true` and
  `resume_action = "pass"`

#### Scenario: ARE-S2 happy path: action=fix-needed dispatches VERIFY_FIX_NEEDED

- **GIVEN** a REQ row with `state='escalated'` and
  `context.verifier_stage='dev_cross_check'` and
  `context.verifier_fixer='dev'`
- **WHEN** the client sends `POST /admin/req/REQ-X/resume` with body
  `{"action": "fix-needed"}`
- **THEN** the endpoint MUST call `engine.step` exactly once with
  `event=Event.VERIFY_FIX_NEEDED`
- **AND** the response MUST be 200 with body containing
  `action == "resumed"` and `event == "verify.fix-needed"`

### Requirement: /admin/req/{req_id}/resume rejects non-ESCALATED states with HTTP 409

The endpoint MUST return HTTP 409 Conflict when the REQ is in any state
other than `escalated`. The error body MUST include the current state
name and a hint pointing at `/admin/req/{req_id}/escalate` as the
prerequisite step. This precondition prevents accidentally re-firing
verifier events on an in-flight stage that already has a transition
queued (e.g. ANALYZING during a running BKD analyze-agent session).

#### Scenario: ARE-S3 calling resume on an in-flight REQ returns 409

- **GIVEN** a REQ row with `state='analyzing'`
- **WHEN** the client sends `POST /admin/req/REQ-X/resume` with body
  `{"action": "pass"}`
- **THEN** the response MUST be 409 with detail containing the literal
  substring `analyzing`
- **AND** `engine.step` MUST NOT be called
- **AND** no SQL UPDATE on `req_state.context` MUST happen

### Requirement: /admin/req/{req_id}/resume returns 404 when the REQ is unknown

The endpoint MUST return HTTP 404 Not Found when no row matches the
`req_id` path parameter, with the same error shape as the other
`/admin/req/{req_id}/*` endpoints (`{"detail": "req <id> not found"}`).
This MUST happen before any state-precondition check, so that "no such
REQ" cannot be confused with "REQ in wrong state".

#### Scenario: ARE-S4 unknown REQ returns 404

- **GIVEN** no row in `req_state` with `req_id='REQ-DOES-NOT-EXIST'`
- **WHEN** the client sends `POST /admin/req/REQ-DOES-NOT-EXIST/resume`
  with any body
- **THEN** the response MUST be 404 with detail containing the literal
  substring `not found`

### Requirement: /admin/req/{req_id}/resume rejects action=pass without resolvable verifier_stage

The endpoint MUST return HTTP 400 Bad Request when `action == "pass"`
and neither `body.stage` nor `req_state.context.verifier_stage` is set,
because `apply_verify_pass` would otherwise emit `VERIFY_ESCALATE`
(self-loop back to ESCALATED) silently. The error MUST instruct the
operator to either pass `body.stage` explicitly or use the BKD verifier
follow-up path. This is required because escalates from non-verifier
paths (e.g. `pr_ci.timeout`, `accept-env-up.fail`, `intake.fail`) leave
the context without `verifier_stage`.

#### Scenario: ARE-S5 action=pass with no verifier_stage returns 400

- **GIVEN** a REQ row with `state='escalated'` and
  `context` containing no `verifier_stage` key (e.g. escalated via
  `pr_ci.timeout`)
- **WHEN** the client sends `POST /admin/req/REQ-X/resume` with body
  `{"action": "pass"}` (no `stage` field)
- **THEN** the response MUST be 400 with detail containing the literal
  substring `verifier_stage`
- **AND** `engine.step` MUST NOT be called

### Requirement: /admin/req/{req_id}/resume body.stage and body.fixer override context fields before dispatch

The endpoint SHALL accept optional `stage` and `fixer` fields in the
request body. When provided, the values MUST be patched into
`req_state.context` (writing keys `verifier_stage` and `verifier_fixer`
respectively) before `engine.step` is invoked, so that
`apply_verify_pass` / `start_fixer` route to the correct stage / fixer
when the prior context did not record them or recorded a different one.
The `fixer` value MUST be one of `{"dev", "spec"}`; any other value MUST
yield HTTP 422 from pydantic schema validation.

#### Scenario: ARE-S6 body.stage patches context.verifier_stage

- **GIVEN** a REQ row with `state='escalated'` and `context.verifier_stage='staging_test'`
- **WHEN** the client sends `POST /admin/req/REQ-X/resume` with body
  `{"action": "pass", "stage": "pr_ci"}`
- **THEN** before `engine.step` is called the endpoint MUST patch
  `context.verifier_stage = "pr_ci"`

### Requirement: /admin/req/{req_id}/resume body.reason is persisted to context for audit

The endpoint SHALL accept an optional `reason` field in the request body.
When provided, the value MUST be persisted in `req_state.context` under
the key `resume_reason`. The flag `resumed_by_admin = true` and
`resume_action = <body.action>` MUST always be patched into context
regardless of whether `reason` is provided, so audit queries can
distinguish admin-driven resumes from BKD-verifier-driven resumes (the
latter writes `verifier_decisions` rows; the former writes only
`req_state.history` + these context fields).

#### Scenario: ARE-S7 body.reason persists to context.resume_reason

- **GIVEN** a REQ row with `state='escalated'` and `context.verifier_stage='staging_test'`
- **WHEN** the client sends `POST /admin/req/REQ-X/resume` with body
  `{"action": "pass", "reason": "GHA infra flake confirmed"}`
- **THEN** before `engine.step` is called the endpoint MUST patch
  `context.resume_reason = "GHA infra flake confirmed"`,
  `context.resumed_by_admin = true`, and `context.resume_action = "pass"`

### Requirement: /admin/req/{req_id}/resume enforces Bearer auth as first step

The endpoint MUST call `webhook._verify_token(authorization)` before any
state read or context write, mirroring `force_escalate`, `complete_req`,
and the runner-ops endpoints. A missing or invalid token MUST yield
HTTP 401 / 403 (whichever `_verify_token` raises) and MUST NOT execute
any DB query or `engine.step` call.

#### Scenario: ARE-S8 missing Bearer token rejects before any state read

- **GIVEN** any state of any REQ
- **WHEN** the client sends `POST /admin/req/REQ-X/resume` without an
  `Authorization` header
- **THEN** `_verify_token` MUST be invoked and MUST raise an HTTPException
- **AND** `req_state.get` MUST NOT be called
- **AND** `engine.step` MUST NOT be called

### Requirement: K8s runner pause/resume admin endpoints renamed with runner- prefix

The orchestrator SHALL rename the pre-existing endpoints `POST /admin/req/{req_id}/pause`
to `POST /admin/req/{req_id}/runner-pause`, and `POST /admin/req/{req_id}/resume`
(the v0.2 runner-Pod recreate operation) to `POST /admin/req/{req_id}/runner-resume`.
The renamed endpoints MUST preserve their existing behavior (delete-Pod-keep-PVC
and recreate-Pod respectively), request body, response shape, and Bearer-token
auth — only the URL path changes. The rename frees the bare `/resume` path for
the new state-level endpoint defined above. Handler function names
(`pause_runner`, `resume_runner`) MUST stay unchanged so existing tests
that import them by name keep working.

#### Scenario: ARE-S9 runner-pause path is registered and old /pause is gone

- **GIVEN** the FastAPI admin router is initialized
- **WHEN** the route table is inspected
- **THEN** the route `POST /admin/req/{req_id}/runner-pause` MUST be
  registered and bound to the `pause_runner` handler
- **AND** no route `POST /admin/req/{req_id}/pause` MUST be registered

#### Scenario: ARE-S10 runner-resume path is registered and bound to runner controller

- **GIVEN** the FastAPI admin router is initialized
- **WHEN** the route table is inspected
- **THEN** the route `POST /admin/req/{req_id}/runner-resume` MUST be
  registered and bound to the `resume_runner` handler (which calls
  `RunnerController.resume`, NOT the new state-level resume)
- **AND** the route `POST /admin/req/{req_id}/resume` MUST be bound to
  the new state-level handler `resume_req`, NOT to `resume_runner`
