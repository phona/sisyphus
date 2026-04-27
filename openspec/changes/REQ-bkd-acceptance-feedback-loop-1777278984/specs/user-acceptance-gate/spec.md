## ADDED Requirements

### Requirement: TEARDOWN_DONE_PASS routes to PENDING_USER_REVIEW

The state machine SHALL replace the prior direct transition from
`ACCEPT_TEARING_DOWN` on `TEARDOWN_DONE_PASS` to `ARCHIVING` with a transition
to a new `PENDING_USER_REVIEW` state. The transition's action MUST be
`post_acceptance_report` (which posts the acceptance summary to the BKD intent
issue without changing its `statusId`). No prior accept teardown logic is
removed; only the next state and action are remapped.

#### Scenario: USER-S1 teardown pass routes to pending-user-review

- **GIVEN** a REQ in state `ACCEPT_TEARING_DOWN`
- **WHEN** `decide(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` is called
- **THEN** the returned `Transition` MUST have `next_state == PENDING_USER_REVIEW` and `action == "post_acceptance_report"`

### Requirement: PENDING_USER_REVIEW exits via user-review.pass and user-review.fix events only

While in `PENDING_USER_REVIEW`, the state machine MUST advance to `ARCHIVING`
(action `done_archive`) when receiving `USER_REVIEW_PASS`, and MUST advance to
`ESCALATED` (action `escalate`) when receiving `USER_REVIEW_FIX`. Any other event
in this state MUST yield a `None` transition (illegal, ignored). The state MUST
NOT have a `SESSION_FAILED` self-loop because no BKD agent is active in this
state.

#### Scenario: USER-S2 user_review_pass routes pending to archiving

- **GIVEN** a REQ in state `PENDING_USER_REVIEW`
- **WHEN** `decide(PENDING_USER_REVIEW, USER_REVIEW_PASS)` is called
- **THEN** the returned `Transition` MUST have `next_state == ARCHIVING` and `action == "done_archive"`

#### Scenario: USER-S3 user_review_fix routes pending to escalated

- **GIVEN** a REQ in state `PENDING_USER_REVIEW`
- **WHEN** `decide(PENDING_USER_REVIEW, USER_REVIEW_FIX)` is called
- **THEN** the returned `Transition` MUST have `next_state == ESCALATED` and `action == "escalate"`

#### Scenario: USER-S4 illegal events from pending yield no transition

- **GIVEN** a REQ in state `PENDING_USER_REVIEW`
- **WHEN** `decide(PENDING_USER_REVIEW, ARCHIVE_DONE)` or `decide(PENDING_USER_REVIEW, SESSION_FAILED)` is called
- **THEN** the function MUST return `None`

### Requirement: webhook derives user-review events from BKD intent issue statusId

The webhook MUST treat BKD `issue.updated` events as user-review signals only when the REQ is in state `PENDING_USER_REVIEW` AND the event's `issueId` equals `ctx.intent_issue_id`; in that case the webhook MUST fetch the issue via the BKD REST client and MUST emit `USER_REVIEW_PASS` when the issue's current `statusId` equals `done`, MUST emit `USER_REVIEW_FIX` (after writing `ctx.escalated_reason = "user-requested-fix"`) when the `statusId` equals `review` or `blocked`, and MUST emit no event for any other `statusId` value. The webhook MUST NOT parse free-text chat content as an approval signal — `statusId` is the only recognised primitive — and MUST NOT emit user-review events for sub-issues sharing the same REQ tag.

#### Scenario: USER-S5 statusId done emits USER_REVIEW_PASS only when state is pending-user-review

- **GIVEN** a REQ in state `PENDING_USER_REVIEW` with `ctx.intent_issue_id = "abc123"`
- **AND** an `issue.updated` webhook arrives with `body.issueId = "abc123"`
- **AND** the BKD client returns the issue with `statusId = "done"`
- **WHEN** the webhook processes the event
- **THEN** `Event.USER_REVIEW_PASS` MUST be emitted to the engine

#### Scenario: USER-S6 statusId review or blocked emits USER_REVIEW_FIX with escalated reason

- **GIVEN** a REQ in state `PENDING_USER_REVIEW` with `ctx.intent_issue_id = "abc123"`
- **AND** an `issue.updated` webhook arrives with `body.issueId = "abc123"`
- **AND** the BKD client returns the issue with `statusId = "review"` (or `"blocked"`)
- **WHEN** the webhook processes the event
- **THEN** `ctx.escalated_reason` MUST be set to `"user-requested-fix"` before `Event.USER_REVIEW_FIX` is emitted

#### Scenario: USER-S7 unknown statusId values do not emit any event

- **GIVEN** a REQ in state `PENDING_USER_REVIEW` with `ctx.intent_issue_id = "abc123"`
- **AND** an `issue.updated` webhook arrives with `body.issueId = "abc123"`
- **AND** the BKD client returns the issue with `statusId = "working"` (or `"todo"` / unknown)
- **WHEN** the webhook processes the event
- **THEN** no `USER_REVIEW_*` event is emitted; the webhook returns a skip outcome

#### Scenario: USER-S8 sub-issue updates do not trigger user-review events

- **GIVEN** a REQ in state `PENDING_USER_REVIEW` with `ctx.intent_issue_id = "abc123"`
- **AND** an `issue.updated` webhook arrives with `body.issueId = "sub456"` (≠ intent issue)
- **WHEN** the webhook processes the event
- **THEN** no `USER_REVIEW_*` event is emitted regardless of the sub-issue's `statusId`

### Requirement: post_acceptance_report writes a managed status block to the intent issue body

The `post_acceptance_report` action MUST PATCH the BKD intent issue's `description`
to embed a managed status block delimited by the marker
`<!-- sisyphus:acceptance-status -->`. The block MUST instruct the user that:

- changing `statusId` to `done` ships the REQ (approve)
- changing `statusId` to `review` or `blocked` triggers fix-needed escalation
- other `statusId` values keep sisyphus waiting

The action MUST be idempotent — repeated invocations replace the existing block
in place rather than appending a new one (use the marker for in-place replacement).
The PATCH MUST NOT include `tags` (avoiding tag replacement semantics) and MUST
NOT include `statusId` (the user owns that field while in PENDING_USER_REVIEW).
The action MUST set `ctx.acceptance_reported_at` to the UTC ISO8601 timestamp of
the call. When `ctx.intent_issue_id` is missing, the action MUST log a warning
and return without raising.

#### Scenario: USER-S9 first invocation appends managed block

- **GIVEN** a REQ with `ctx.intent_issue_id = "abc123"` and `ctx.pr_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/200"}`
- **AND** the intent issue body has no existing acceptance-status block
- **WHEN** `post_acceptance_report` is invoked
- **THEN** the BKD `update_issue` call MUST PATCH `description` to include `<!-- sisyphus:acceptance-status -->` followed by the rendered status block
- **AND** the PATCH MUST NOT include `tags` or `statusId`
- **AND** `ctx.acceptance_reported_at` MUST be set to a non-empty ISO8601 timestamp

#### Scenario: USER-S10 second invocation replaces managed block in place

- **GIVEN** the intent issue body already contains a `<!-- sisyphus:acceptance-status -->` block from a prior invocation
- **WHEN** `post_acceptance_report` is invoked again
- **THEN** the resulting body MUST contain exactly one occurrence of the marker
- **AND** the block content between the marker and the next blank line / EOF MUST be the freshly rendered version

#### Scenario: USER-S11 missing intent_issue_id returns without error

- **GIVEN** a REQ whose `ctx` has no `intent_issue_id`
- **WHEN** `post_acceptance_report` is invoked
- **THEN** no BKD `update_issue` call is made
- **AND** the action returns a non-error result

### Requirement: watchdog skips PENDING_USER_REVIEW state

The watchdog scanner MUST treat `PENDING_USER_REVIEW` the same way it treats
`INTAKING` — the state belongs to the human-loop-conversation taxonomy
documented in `docs/user-feedback-loop.md` §1 and has no BKD agent to crash-check.
Adding `PENDING_USER_REVIEW.value` to `watchdog._SKIP_STATES` is the canonical
mechanism. The watchdog MUST NOT emit `SESSION_FAILED` or `watchdog.stuck` for
REQs sitting in this state regardless of how long they have been there.

#### Scenario: USER-S12 watchdog skip set contains pending_user_review

- **GIVEN** the watchdog module
- **WHEN** `watchdog._SKIP_STATES` is inspected
- **THEN** it MUST contain `ReqState.PENDING_USER_REVIEW.value`
- **AND** it MUST also still contain the prior entries (`DONE`, `ESCALATED`, `GH_INCIDENT_OPEN`, `INIT`, `INTAKING`)
