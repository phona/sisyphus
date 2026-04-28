# intent-status-sync

## ADDED Requirements

### Requirement: engine MUST sync BKD intent issue statusId on terminal transitions

The orchestrator engine SHALL, upon a successful CAS transition into a
terminal state (`ReqState.DONE` or `ReqState.ESCALATED`) from a
non-terminal state, PATCH the BKD intent issue's `statusId` field. The
PATCH value MUST be `done` when the terminal state is `DONE` and
`review` when the terminal state is `ESCALATED`. This sync MUST be
scheduled fire-and-forget (asyncio.Task) alongside the existing
`_cleanup_runner_on_terminal` task in `engine.step` so it does not block
the webhook response. The sync MUST be skipped when `ctx.intent_issue_id`
is absent or empty (defensive — webhook always populates it on first
delivery, but tests / replay paths may lack it).

#### Scenario: HITL-S1 transition into DONE patches intent statusId="done"

- **GIVEN** a REQ in state `ARCHIVING` with `ctx.intent_issue_id="abc123"`
  and `project_id="proj-x"`
- **WHEN** `engine.step` processes `Event.ARCHIVE_DONE` and the CAS to
  `ReqState.DONE` succeeds
- **THEN** an asyncio.Task MUST be scheduled that calls
  `BKDClient.update_issue(project_id="proj-x", issue_id="abc123",
  status_id="done")`
- **AND** the task MUST be scheduled, not awaited, so `engine.step`
  returns without blocking on the BKD PATCH

#### Scenario: HITL-S2 transition into ESCALATED patches intent statusId="review"

- **GIVEN** a REQ in state `REVIEW_RUNNING` with
  `ctx.intent_issue_id="abc123"` and `project_id="proj-x"`
- **WHEN** `engine.step` processes `Event.VERIFY_ESCALATE` and the CAS
  to `ReqState.ESCALATED` succeeds
- **THEN** an asyncio.Task MUST be scheduled that calls
  `BKDClient.update_issue(project_id="proj-x", issue_id="abc123",
  status_id="review")`

### Requirement: escalate MUST sync intent statusId on SESSION_FAILED self-loop CAS

The `actions.escalate` handler MUST, after its internal
`cas_transition(... ReqState.ESCALATED, Event.SESSION_FAILED, "escalate")`
returns `advanced=True`, PATCH the BKD intent issue's `statusId` to
`review`. The PATCH MUST be awaited (not fire-and-forget) because the
escalate handler is already in an async context performing
`cleanup_runner` await; an additional await is the simpler shape and
avoids spawning a Task that may outlive the event handling. Any
exception from the PATCH MUST be caught and logged at WARNING level
with `req_id` and `error` fields — it MUST NOT propagate out of
`escalate`.

#### Scenario: HITL-S3 escalate self-loop CAS to ESCALATED triggers intent statusId="review"

- **GIVEN** a REQ with `body.event="watchdog.stuck"`,
  `ctx.intent_issue_id="abc123"`, current state `STAGING_TEST_RUNNING`
- **WHEN** `escalate` reaches its SESSION_FAILED self-loop block
  (line ~434), the inner CAS to `ESCALATED` succeeds, and `cleanup_runner`
  finishes
- **THEN** `BKDClient.update_issue` MUST be called with
  `project_id=body.projectId, issue_id="abc123", status_id="review"`
- **AND** if that PATCH raises, the exception MUST be caught and logged;
  `escalate` MUST still return its normal result dict
  (`{"escalated": True, ...}`)

### Requirement: PR-merged shortcut MUST NOT double-PATCH intent statusId

The new `_sync_intent_status_on_terminal` helper MUST NOT be invoked
from the `_apply_pr_merged_done_override` code path. That path already
PATCHes the BKD intent issue's `statusId` to `done` via
`bkd.merge_tags_and_update(... status_id="done")` (existing behavior,
not modified by this REQ); calling the new helper there would issue a
redundant second PATCH for no benefit (BKD statusId PATCH is idempotent
— a duplicate write is benign but wastes a network round-trip on the
hot path). The override path uses `req_state.cas_transition` directly
and never re-enters `engine.step` for that transition, so this
invariant holds naturally as long as the new helper stays in
`engine.step` + the SESSION_FAILED self-loop branch in `escalate.py`.

#### Scenario: HITL-S5 PR-merged shortcut does not invoke the new sync helper

- **GIVEN** a REQ in state `ACCEPT_RUNNING` whose `feat/REQ-X` PR is
  merged, and `escalate` is invoked
- **WHEN** `_apply_pr_merged_done_override` returns successfully
- **THEN** the new `_sync_intent_status_on_terminal` helper MUST NOT be
  called as part of that code path
- **AND** the existing `bkd.merge_tags_and_update(...status_id="done")`
  call MUST still fire (existing behavior)

### Requirement: BKD PATCH failure MUST NOT block the state transition

The intent statusId PATCH MUST be best-effort. Any exception raised by
`BKDClient.update_issue` (httpx error, BKD 5xx, JSON decode failure,
connection refused) MUST be caught inside the helper and logged at
WARNING level with `req_id`, `intent_issue_id`, `target_status_id`, and
`error` fields. The state machine transition MUST NOT be rolled back.
The runner cleanup task MUST proceed regardless. The downstream
`req_state.state` value is the source of truth; BKD board statusId is
a UX mirror that may temporarily lag.

#### Scenario: HITL-S4 BKD PATCH failure logs warning and lets state machine continue

- **GIVEN** a REQ transitioning into `DONE` and BKD localhost responding
  with HTTP 503
- **WHEN** the sync helper's `BKDClient.update_issue` call raises
- **THEN** the helper MUST log a `engine.intent_status_sync_failed`
  WARNING with `req_id`, `intent_issue_id`, `target_status_id="done"`,
  and the error string
- **AND** the helper MUST NOT re-raise
- **AND** `req_state.state` MUST remain `DONE` (no rollback)
- **AND** `_cleanup_runner_on_terminal` MUST still be invoked

### Requirement: self-loop and intermediate transitions MUST NOT trigger the sync

The engine MUST NOT call `_sync_intent_status_on_terminal` for any
transition where `cur_state == next_state` (self-loop, e.g.
`apply_verify_pass` that keeps the engine surface state unchanged
before the action's internal CAS) OR where `next_state` is not in
`{DONE, ESCALATED}`. The sync MUST fire only on the **edge** from a
non-terminal `cur_state` to a terminal `next_state` — intermediate
stage transitions and self-loops MUST be silent w.r.t. BKD intent
statusId.

#### Scenario: HITL-S6 self-loop transition does not invoke the sync helper

- **GIVEN** a transition `(REVIEW_RUNNING, VERIFY_PASS) → REVIEW_RUNNING`
  declared as a self-loop in `state.py`
- **WHEN** `engine.step` processes the event and CAS succeeds
- **THEN** the new `_sync_intent_status_on_terminal` helper MUST NOT be
  called
- **AND** no `BKDClient.update_issue(... status_id=...)` call MUST be
  made by the engine for this transition (downstream actions may still
  PATCH on their own, that is out of scope here)
