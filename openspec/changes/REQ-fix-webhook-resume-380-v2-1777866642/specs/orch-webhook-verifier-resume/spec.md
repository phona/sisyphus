## ADDED Requirements

### Requirement: webhook bypasses dedup for verifier resume when state is REVIEW_RUNNING

The orchestrator webhook handler MUST bypass the event dedup `skip` decision and proceed
to verifier-decision parsing **only** when **all four** of the following conditions hold
simultaneously:

1. `body.event == "session.completed"`,
2. the issue tags fetched (or already present on the payload) contain `"verifier"`,
3. `dedup.check_and_record` returned `"skip"` for the computed event_id, and
4. the current REQ row state SHALL equal `ReqState.REVIEW_RUNNING`.

This bypass exists to recover from BKD `session.completed` deliveries that re-use the
prior `executionId` after a follow-up resume on the same verifier issue. The state guard
is mandatory: VERIFY_PASS / VERIFY_FIX_NEEDED / VERIFY_ESCALATE all transition the REQ
**out of** `REVIEW_RUNNING`, so a stale BKD redelivery that arrives after the state has
advanced MUST continue to be skipped (no retroactive re-decision). When the bypass fires,
the handler MUST emit a structured log `webhook.dedup.verifier_resume_bypass` carrying
`event_id`, `executionId`, and `req_id`, plus an `obs.record_event` row of type
`dedup.verifier_resume_bypass` keyed on the same fields.

For events that pass the early noise filter (REQ-tagged or `intent:*` issues — i.e.,
events the orchestrator actually processes), the handler MUST emit an
`obs.record_event` row of type `webhook.dedup.observed` whose extras carry
`{event_id, executionId, status}` capturing the dedup outcome. This is
observability-only — it does not change control flow, but lets future incidents
correlate verifier-decision drops to a specific dedup status + executionId without
re-deploying. Noise events (no REQ tag / no intent tag) MUST NOT emit this row, to
keep the contract from REQ-router-noise-filter-1777109307 intact.

#### Scenario: VWR-S1 same-executionId redelivery on verifier issue while REVIEW_RUNNING bypasses dedup
- **GIVEN** a verifier-issue session.completed has already been processed (dedup row
  exists, processed_at set), the REQ is in state `REVIEW_RUNNING`, and the verifier's
  last assistant message now contains `"action": "pass"`
- **WHEN** the webhook receives a second `session.completed` carrying the **same**
  `executionId` (so dedup returns `skip`), with `verifier` in the issue tags
- **THEN** the handler MUST treat this as a verifier resume, log
  `webhook.dedup.verifier_resume_bypass`, parse the verifier decision JSON, and call
  `engine.step` with `Event.VERIFY_PASS`

#### Scenario: VWR-S2 stale redelivery after state advances continues to skip
- **GIVEN** the REQ has already left `REVIEW_RUNNING` (e.g., advanced to
  `STAGING_TEST_RUNNING`) and a verifier-issue session.completed dedup row exists
- **WHEN** BKD redelivers the same `session.completed` (dedup → `skip`)
- **THEN** the handler MUST honour the dedup `skip` and return without emitting any
  state-machine event — protecting the already-advanced REQ from a retroactive verifier
  decision

#### Scenario: VWR-S3 dedup status observability event emitted for processed events
- **GIVEN** an inbound webhook on a REQ-tagged session.completed (event survives the
  noise filter) and a dedup outcome of `new` or `retry`
- **WHEN** the handler reaches the post-noise-filter observability step
- **THEN** it MUST call `obs.record_event("webhook.dedup.observed", ...)` with extras
  containing the resolved `event_id`, the payload's `executionId`, and the dedup
  `status` literal. Noise events that the early filter discards MUST NOT emit this
  row.

### Requirement: admin endpoint POST /admin/req/{req_id}/retrigger-verifier re-runs verifier consumption

The orchestrator MUST expose `POST /admin/req/{req_id}/retrigger-verifier` (auth: same
`Authorization: Bearer <webhook_token>` as other admin endpoints). The request body
MUST be `{"issue_id": "<bkd_issue_id>"}` where `issue_id` is the BKD verifier issue
whose decision JSON should be re-read.

Behavior:

1. The endpoint MUST 404 when `req_id` does not exist in `req_state`.
2. The endpoint MUST 400 when `body.issue_id` is empty / missing.
3. On valid input, the handler MUST fetch the BKD issue's last assistant message via
   `BKDClient.get_last_assistant_message(project_id, issue_id)` and pass it (together
   with the BKD issue's tags) to
   `router.derive_verifier_event_with_retry_info(decision_source, tags)`.
4. If the parser returns a verifier-routing `Event` (`VERIFY_PASS`, `VERIFY_FIX_NEEDED`,
   or `VERIFY_ESCALATE`), the handler MUST call `engine.step` with that event using a
   `_FakeBody`-style stub (consistent with `/admin/req/{req_id}/emit`) and return the
   engine result plus the parsed decision payload. It MUST also write a
   `verifier_decisions` row best-effort (failures logged, not raised).
5. If the parser cannot extract a verifier event (no JSON / malformed JSON / unknown
   action), the endpoint MUST return HTTP 422 with `detail` naming the parse reason
   surfaced by the router; it MUST NOT alter REQ state.

This endpoint is the operator-facing escape hatch when the standard webhook path drops
a verifier resume (e.g., BKD redelivery semantics differ from expectations). It is
explicitly distinct from `POST /admin/req/{req_id}/resume`, which takes a verbatim
state-level event from the operator and does **not** read BKD chat.

#### Scenario: VWR-S4 retrigger-verifier reads BKD chat and emits VERIFY_PASS
- **GIVEN** a REQ in `REVIEW_RUNNING`, a BKD verifier issue whose latest assistant
  message contains a valid `{"action": "pass", ...}` JSON block, and the operator
  presents a valid `Authorization: Bearer <webhook_token>` header
- **WHEN** they `POST /admin/req/{req_id}/retrigger-verifier` with body
  `{"issue_id": "<bkd-issue-id>"}`
- **THEN** the handler MUST call `engine.step` with `Event.VERIFY_PASS` and return a
  200 response whose body includes the parsed decision payload and the engine result

#### Scenario: VWR-S5 retrigger-verifier returns 422 when decision cannot be parsed
- **GIVEN** a verifier issue whose last assistant message contains no JSON block (or a
  malformed one)
- **WHEN** the operator calls retrigger-verifier
- **THEN** the handler MUST return 422 with a `detail` string surfaced from the router's
  parse-failure reason, and it MUST NOT modify REQ state nor call `engine.step`
