## ADDED Requirements

### Requirement: PENDING_USER_ACCEPT state pauses the pipeline between teardown and archive

The orchestrator SHALL pause every REQ in a new `PENDING_USER_ACCEPT`
state after `teardown_accept_env` finishes, and MUST NOT auto-advance to
`ARCHIVING` without a user-driven event. The state machine transition
table at `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` MUST point to
`ReqState.PENDING_USER_ACCEPT` with action `post_acceptance_report`. The state machine transition table at
`(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` MUST point to
`ReqState.PENDING_USER_ACCEPT` with action `post_acceptance_report`. The
engine MUST emit no further chained events after this transition — the
REQ deliberately waits for the next user-driven webhook. This SHALL hold
even when `settings.skip_archive=True`, because the user's accept signal
is orthogonal to whether `done_archive` actually merges PRs.

#### Scenario: BAFL-S1 teardown_done_pass routes to PENDING_USER_ACCEPT, not ARCHIVING

- **GIVEN** the state machine transition table from `orchestrator.state.TRANSITIONS`
- **WHEN** `decide(ReqState.ACCEPT_TEARING_DOWN, Event.TEARDOWN_DONE_PASS)` is called
- **THEN** the returned `Transition.next_state` MUST equal
  `ReqState.PENDING_USER_ACCEPT`, and `Transition.action` MUST equal
  `"post_acceptance_report"` (NOT the legacy `"done_archive"`)

### Requirement: ACCEPT_USER_APPROVED routes PENDING_USER_ACCEPT to ARCHIVING

The state machine MUST register a transition
`(PENDING_USER_ACCEPT, ACCEPT_USER_APPROVED) → (ARCHIVING, done_archive)`.
This MUST be the only path from `PENDING_USER_ACCEPT` that ends in a
`DONE` terminal: no other event SHALL trigger `done_archive` from
`PENDING_USER_ACCEPT`.

#### Scenario: BAFL-S2 user approves → archive

- **GIVEN** the transition table
- **WHEN** `decide(ReqState.PENDING_USER_ACCEPT, Event.ACCEPT_USER_APPROVED)` runs
- **THEN** `next_state` MUST be `ReqState.ARCHIVING` and `action` MUST be
  `"done_archive"`

### Requirement: ACCEPT_USER_REQUEST_CHANGES routes PENDING_USER_ACCEPT to FIXER_RUNNING

The state machine MUST register a transition
`(PENDING_USER_ACCEPT, ACCEPT_USER_REQUEST_CHANGES) → (FIXER_RUNNING, start_fixer)`.
The reuse of `start_fixer` is intentional: the webhook layer (see
requirement below) MUST pre-populate `ctx.verifier_stage="accept"`,
`ctx.verifier_fixer="dev"`, and `ctx.verifier_reason=<user feedback>`
before this event fires, so `start_fixer` can construct the fixer issue
identically to a verifier-driven fix round.

#### Scenario: BAFL-S3 user requests changes → fixer

- **GIVEN** the transition table
- **WHEN** `decide(ReqState.PENDING_USER_ACCEPT, Event.ACCEPT_USER_REQUEST_CHANGES)` runs
- **THEN** `next_state` MUST be `ReqState.FIXER_RUNNING` and `action` MUST
  be `"start_fixer"`

### Requirement: ACCEPT_USER_REJECTED routes PENDING_USER_ACCEPT to ESCALATED

The state machine MUST register a transition
`(PENDING_USER_ACCEPT, ACCEPT_USER_REJECTED) → (ESCALATED, escalate)`.
The action `escalate` MUST receive a body / context whose
`escalated_reason` resolves to `"user-rejected-acceptance"`. This reason
MUST NOT be classified as transient by `escalate._is_transient` —
rejection by the user is hard and MUST NOT be auto-resumed.

#### Scenario: BAFL-S4 user rejects → escalate (non-transient)

- **GIVEN** the transition table and `escalate._is_transient`
- **WHEN** `decide(ReqState.PENDING_USER_ACCEPT, Event.ACCEPT_USER_REJECTED)` runs
- **THEN** `next_state` MUST be `ReqState.ESCALATED`, `action` MUST be
  `"escalate"`, AND `escalate._is_transient(body_event="issue.updated",
  reason="user-rejected-acceptance")` MUST return `False`

### Requirement: SESSION_FAILED self-loop covers PENDING_USER_ACCEPT

The state machine MUST register a `SESSION_FAILED` self-loop transition
on `PENDING_USER_ACCEPT`, matching every other in-flight running state.
The state SHALL NOT be removed by the engine when this self-loop fires;
the `escalate` action it dispatches owns the decision of whether to
actually advance to `ESCALATED`.
`(PENDING_USER_ACCEPT, SESSION_FAILED) → (PENDING_USER_ACCEPT, escalate)`,
matching every other in-flight state. The `escalate` action MUST decide
whether to actually transition to `ESCALATED` based on its existing
auto-resume / hard-reason heuristics; `PENDING_USER_ACCEPT` itself MUST
NOT be removed by the engine on this self-loop.

#### Scenario: BAFL-S5 SESSION_FAILED on PENDING_USER_ACCEPT runs escalate as a self-loop

- **GIVEN** the transition table
- **WHEN** `decide(ReqState.PENDING_USER_ACCEPT, Event.SESSION_FAILED)` runs
- **THEN** the returned `Transition` MUST be non-None, `next_state` MUST
  equal `ReqState.PENDING_USER_ACCEPT` (self-loop), and `action` MUST
  equal `"escalate"`

### Requirement: Webhook routes BKD intent issue tags to ACCEPT_USER_* events while in PENDING_USER_ACCEPT

The webhook handler MUST resolve `issue.updated` events on the intent
issue while the REQ is in `PENDING_USER_ACCEPT` to one of three
`ACCEPT_USER_*` events using the rules below, in order of precedence,
BEFORE the existing `router.derive_event` is consulted:

1. `acceptance:approve` in tags → `Event.ACCEPT_USER_APPROVED`
2. `acceptance:request-changes` in tags → `Event.ACCEPT_USER_REQUEST_CHANGES`
3. `acceptance:reject` in tags → `Event.ACCEPT_USER_REJECTED`
4. `body.changes.statusId == "done"` (i.e. user closed the BKD issue)
   AND none of the three `acceptance:*` tags is set →
   `Event.ACCEPT_USER_REJECTED`

If none of these conditions hold, the webhook SHALL fall through to the
existing `derive_event` path. The state-aware shortcut MUST run BEFORE
`router.derive_event`, because `derive_event` has no notion of `cur_state`
and `acceptance:*` tags would otherwise be silently dropped.

#### Scenario: BAFL-S6 acceptance:approve tag while in PENDING emits ACCEPT_USER_APPROVED

- **GIVEN** a webhook for `issue.updated` on the intent issue,
  `cur_state = ReqState.PENDING_USER_ACCEPT`, and tags `["REQ-x",
  "acceptance:approve"]`
- **WHEN** `webhook._derive_pending_user_accept_event` (or the inline
  shortcut) is evaluated
- **THEN** the returned `Event` MUST be `Event.ACCEPT_USER_APPROVED`

#### Scenario: BAFL-S7 statusId=done with no acceptance tag emits ACCEPT_USER_REJECTED

- **GIVEN** a webhook for `issue.updated` on the intent issue,
  `cur_state = ReqState.PENDING_USER_ACCEPT`, tags `["REQ-x"]`, and
  `body.changes = {"statusId": "done"}`
- **WHEN** the shortcut is evaluated
- **THEN** the returned `Event` MUST be `Event.ACCEPT_USER_REJECTED`

### Requirement: Webhook pre-populates ctx.verifier_* before emitting ACCEPT_USER_REQUEST_CHANGES

When the webhook decides to emit `ACCEPT_USER_REQUEST_CHANGES`, it MUST
update the REQ context with at minimum these three keys before calling
`engine.step`:

- `verifier_stage = "accept"`
- `verifier_fixer = "dev"`
- `verifier_reason = <user feedback text>` — sourced from the **last
  user-authored** chat message on the intent issue (best-effort; if BKD
  REST cannot retrieve it, the value MAY be the empty string)

These keys are exactly what `start_fixer` reads via the existing
verifier-driven path; reusing them lets `start_fixer` work unchanged.

#### Scenario: BAFL-S8 ACCEPT_USER_REQUEST_CHANGES sets verifier_stage/fixer in ctx

- **GIVEN** a webhook for `issue.updated`, intent issue, tags
  `["REQ-x", "acceptance:request-changes"]`, `cur_state =
  PENDING_USER_ACCEPT`
- **WHEN** the webhook routes to `ACCEPT_USER_REQUEST_CHANGES`
- **THEN** before `engine.step` is invoked, `req_state.update_context`
  MUST have been awaited with a patch containing exactly the keys
  `verifier_stage="accept"`, `verifier_fixer="dev"`, AND
  `verifier_reason` (string, may be empty)

### Requirement: post_acceptance_report action publishes the user-facing block

The new action `post_acceptance_report` MUST be registered in
`actions.REGISTRY` under the name `"post_acceptance_report"`. When
invoked it MUST:

1. Render a Jinja template `_pending_user_accept.md.j2` that includes
   the canonical "sisyphus acceptance" header text + the three tag
   instructions (`acceptance:approve` / `acceptance:request-changes` /
   `acceptance:reject`).
2. PATCH the BKD intent issue body to embed the rendered block (the
   block MUST be wrapped in HTML comment markers
   `<!-- sisyphus:acceptance-report -->` / `<!-- /sisyphus:acceptance-report -->`
   so a follow-up call can replace the block instead of appending).
3. Merge the BKD intent issue tags so `acceptance:pending` is added (and
   any prior `accept` tag removed if present).
4. Set the BKD intent issue `statusId="review"` so it surfaces on the
   BKD board's review column.
5. Persist the rendered block into `ctx.acceptance_report` for downstream
   fixer / verifier prompts to reuse.

If any BKD REST call fails the action MUST log a warning but MUST NOT
raise — the REQ has already advanced to `PENDING_USER_ACCEPT`, and a
failed-to-post report can be re-posted via admin retry without
rolling back state.

#### Scenario: BAFL-S9 post_acceptance_report is idempotent under partial BKD failure

- **GIVEN** `post_acceptance_report` is invoked, AND the
  `update_issue` BKD call raises `RuntimeError("BKD 5xx")`
- **WHEN** the action handler runs
- **THEN** the handler MUST NOT raise out, and the returned dict MUST be
  a non-`{"emit": ...}` shape (no chained event), AND `ctx.acceptance_report`
  MUST still have been updated via `req_state.update_context` (best-effort
  persistence). A subsequent re-invocation MUST overwrite the block at
  the same `<!-- sisyphus:acceptance-report -->` markers, not append a
  duplicate.

### Requirement: Watchdog skips PENDING_USER_ACCEPT

`watchdog.py` MUST NOT escalate REQs in `PENDING_USER_ACCEPT`. The state
SHALL be added to a `_NO_WATCHDOG_STATES` module-level set whose values
are unioned into the SQL pre-filter alongside `_SKIP_STATES`. The
resulting query MUST exclude rows whose `state` is `pending-user-accept`
even after `watchdog_stuck_threshold_sec` (default 60min) have elapsed
since `updated_at`. This is the policy contract that makes
"user can leave a REQ in review for days" safe.

#### Scenario: BAFL-S10 watchdog._tick excludes PENDING_USER_ACCEPT row from rows pre-filter

- **GIVEN** a fake DB pool whose `fetch` records the SQL parameters and
  returns an empty list, AND a row in the underlying source at
  `state="pending-user-accept"` with `updated_at` 2 hours ago
- **WHEN** `watchdog._tick` calls `pool.fetch` to pre-filter stuck REQs
- **THEN** the parameter list passed to `fetch` MUST include the string
  `"pending-user-accept"` in the excluded states (i.e. inside the
  `state <> ALL($1)` parameter), demonstrating the state is in the
  watchdog's skip set
