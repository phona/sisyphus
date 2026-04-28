## ADDED Requirements

### Requirement: PR merged hook triggers archive closed loop

When a human reviewer merges a sisyphus-managed PR on GitHub, the orchestrator
SHALL receive a notification via `POST /admin/req/{req_id}/pr-merged` and SHALL
emit `Event.PR_MERGED` through the state machine to trigger the `done_archive`
action. The endpoint MUST be authenticated with a Bearer token identical to other
admin endpoints.

The state machine MUST define `Event.PR_MERGED` with transitions from
`PENDING_USER_REVIEW`, `REVIEW_RUNNING`, and `PR_CI_RUNNING` to `ARCHIVING`
with action `done_archive`. For terminal states (`done`, `escalated`) the endpoint
MUST return a 200 noop response without modifying state. For all other states the
endpoint MUST return 409.

#### Scenario: PMH-S1 state pending-user-review emits PR_MERGED and calls done_archive

- **GIVEN** a REQ in state `pending-user-review`
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called with valid Bearer token and merge metadata
- **THEN** `update_context` is called with `merged_pr_url`, `merged_sha`, `merged_at`, `pr_merged_trigger=gha-hook`
- **AND** `engine.step` is called with `event=Event.PR_MERGED` and `cur_state=pending-user-review`
- **AND** response body has `action=pr_merged` and `from_state=pending-user-review`

#### Scenario: PMH-S2 state review-running emits PR_MERGED

- **GIVEN** a REQ in state `review-running`
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called with valid token
- **THEN** `engine.step` is called with `event=Event.PR_MERGED` and `cur_state=review-running`
- **AND** response has `action=pr_merged`

#### Scenario: PMH-S3 state pr-ci-running emits PR_MERGED

- **GIVEN** a REQ in state `pr-ci-running`
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called with valid token
- **THEN** `engine.step` is called with `event=Event.PR_MERGED` and `cur_state=pr-ci-running`

#### Scenario: PMH-S4 state done returns noop without DB write

- **GIVEN** a REQ already in state `done`
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called
- **THEN** response is 200 with `action=noop`
- **AND** `engine.step` MUST NOT be called
- **AND** `update_context` MUST NOT be called

#### Scenario: PMH-S5 state escalated returns noop without DB write

- **GIVEN** a REQ in state `escalated`
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called
- **THEN** response is 200 with `action=noop`
- **AND** no state mutation occurs

#### Scenario: PMH-S6 unexpected state returns 409

- **GIVEN** a REQ in state `analyzing`
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called
- **THEN** response is 409 with detail mentioning the current state
- **AND** `engine.step` MUST NOT be called

#### Scenario: PMH-S7 missing or bad token returns 401

- **GIVEN** no Authorization header or invalid token
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called
- **THEN** response is 401
- **AND** `req_state.get` MUST NOT be called

#### Scenario: PMH-S8 unknown req id returns 404

- **GIVEN** req_id not present in the database
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called with valid token
- **THEN** response is 404 with detail containing "not found"

#### Scenario: PMH-S9 context patch includes all required merge fields

- **GIVEN** a REQ in state `pending-user-review`
- **WHEN** `POST /admin/req/{req_id}/pr-merged` is called with body `{merged_pr_url, merged_sha, merged_at}`
- **THEN** `update_context` patch MUST include all four fields: `merged_pr_url`, `merged_sha`, `merged_at`, `pr_merged_trigger=gha-hook`

#### Scenario: PMH-S10 route is registered at correct path

- **GIVEN** the FastAPI admin router
- **WHEN** POST routes are enumerated
- **THEN** `/admin/req/{req_id}/pr-merged` MUST be present and bound to the `pr_merged` function

### Requirement: PR_MERGED event wired in state machine for three source states

The state machine SHALL contain `Event.PR_MERGED` with transitions from
`PENDING_USER_REVIEW`, `REVIEW_RUNNING`, and `PR_CI_RUNNING`. Each transition
MUST target `ReqState.ARCHIVING` with action `done_archive`.

#### Scenario: PMH-SM1 TRANSITIONS table contains PR_MERGED for all three states

- **GIVEN** the TRANSITIONS dict in state.py
- **WHEN** keys are inspected
- **THEN** `(PENDING_USER_REVIEW, PR_MERGED)`, `(REVIEW_RUNNING, PR_MERGED)`, and `(PR_CI_RUNNING, PR_MERGED)` MUST all be present with `next_state=ARCHIVING` and `action=done_archive`
