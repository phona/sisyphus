# bkd-intent-status-sync

## ADDED Requirements

### Requirement: intent_status helper MUST map terminal ReqState to BKD statusId

The system SHALL provide a single source of truth mapping each terminal
sisyphus `ReqState` to a BKD kanban `statusId` value. The mapping MUST be
defined in `orchestrator.intent_status.STATE_TO_STATUS_ID` and exposed via
`status_id_for(state) -> str | None`. The function MUST return `"done"`
for `ReqState.DONE`, MUST return `"review"` for `ReqState.ESCALATED`, and
MUST return `None` for every non-terminal state. Non-terminal callers
SHALL receive `None` so they can skip the PATCH without raising.

#### Scenario: BIS-S1 status_id_for(DONE) returns "done"

- **GIVEN** terminal state `ReqState.DONE`
- **WHEN** `intent_status.status_id_for(state)` is called
- **THEN** the return value MUST equal the literal string `"done"`

#### Scenario: BIS-S2 status_id_for(ESCALATED) returns "review"

- **GIVEN** terminal state `ReqState.ESCALATED`
- **WHEN** `intent_status.status_id_for(state)` is called
- **THEN** the return value MUST equal the literal string `"review"`

#### Scenario: BIS-S3 status_id_for non-terminal state returns None

- **GIVEN** any non-terminal state (e.g. `ReqState.INTAKING`, `ReqState.ANALYZING`,
  `ReqState.SPEC_LINT_RUNNING`, `ReqState.REVIEW_RUNNING`)
- **WHEN** `intent_status.status_id_for(state)` is called
- **THEN** the return value MUST be `None`

### Requirement: patch_terminal_status MUST PATCH BKD intent issue statusId on terminal entry

The system SHALL provide an async helper
`intent_status.patch_terminal_status(*, project_id, intent_issue_id,
terminal_state, source)` that PATCHes the BKD intent issue's `statusId`
field via `BKDClient.update_issue`. The helper MUST resolve the target
`statusId` via `status_id_for(terminal_state)`. When the resolved value
is `None` (non-terminal state), or when `intent_issue_id` is empty / None,
the helper MUST skip the PATCH and return `False` without raising. When
both inputs are valid, the helper MUST issue exactly one
`bkd.update_issue(project_id=..., issue_id=intent_issue_id,
status_id=<mapped>)` call.

#### Scenario: BIS-S4 DONE entry PATCHes BKD with status_id="done"

- **GIVEN** `intent_issue_id="intent-1"`, `project_id="proj-x"`,
  `terminal_state=ReqState.DONE`
- **WHEN** `await intent_status.patch_terminal_status(...)` runs
- **THEN** `BKDClient.update_issue` MUST be called exactly once with
  `project_id="proj-x"`, `issue_id="intent-1"`, `status_id="done"`
- **AND** the helper MUST return a truthy value indicating PATCH was attempted

#### Scenario: BIS-S5 ESCALATED entry PATCHes BKD with status_id="review"

- **GIVEN** `intent_issue_id="intent-1"`, `project_id="proj-x"`,
  `terminal_state=ReqState.ESCALATED`
- **WHEN** `await intent_status.patch_terminal_status(...)` runs
- **THEN** `BKDClient.update_issue` MUST be called exactly once with
  `project_id="proj-x"`, `issue_id="intent-1"`, `status_id="review"`

#### Scenario: BIS-S6 missing intent_issue_id skips BKD call

- **GIVEN** `intent_issue_id=None` (or empty string)
- **WHEN** `await intent_status.patch_terminal_status(...)` runs
- **THEN** `BKDClient.update_issue` MUST NOT be called
- **AND** the helper MUST return `False`
- **AND** no exception MUST propagate

#### Scenario: BIS-S7 non-terminal state skips BKD call

- **GIVEN** `terminal_state=ReqState.INTAKING` (or any other non-terminal)
- **WHEN** `await intent_status.patch_terminal_status(...)` runs
- **THEN** `BKDClient.update_issue` MUST NOT be called
- **AND** the helper MUST return `False`

### Requirement: patch_terminal_status MUST tolerate BKD failures without raising

The helper MUST catch every exception raised by `BKDClient.update_issue`
(or by entering / exiting the BKD client context manager). On failure
the helper MUST log at WARNING level with key `intent_status.patch_failed`
and fields `{project_id, intent_issue_id, status_id, source, error}`,
then return without re-raising. State machine code that calls this
helper MUST NOT see BKD-related exceptions propagate, so REQ progress
and runner cleanup are unaffected by BKD reachability problems.

#### Scenario: BIS-S8 BKD raises - helper logs warning and swallows

- **GIVEN** `intent_issue_id="intent-1"`, `terminal_state=ReqState.DONE`,
  and a `BKDClient.update_issue` mock that raises `RuntimeError("BKD 503")`
- **WHEN** `await intent_status.patch_terminal_status(...)` runs
- **THEN** no exception MUST propagate out of the helper
- **AND** the helper MUST log a warning record whose event key is
  `intent_status.patch_failed`

### Requirement: engine.step MUST sync intent statusId on terminal transition

`engine.step` MUST, after a successful CAS into a terminal state
(`transition.next_state in {ReqState.DONE, ReqState.ESCALATED}` AND
`cur_state` is itself non-terminal), invoke
`intent_status.patch_terminal_status(...)`. The intent issue id MUST be
read from `ctx.get("intent_issue_id")`; when absent, the helper itself
handles the skip path. The helper call MUST be `await`ed (not
fire-and-forget) so the PATCH lands BEFORE the transition's action
handler runs — this preserves ordering for paths whose action handler
may issue its own statusId PATCH inside the body (e.g.
`_apply_pr_merged_done_override` writes `status_id="done"` after engine
already wrote `status_id="review"`; the await guarantees final state is
`"done"`).

#### Scenario: BIS-S9 ARCHIVING + ARCHIVE_DONE → DONE PATCHes intent statusId="done"

- **GIVEN** a REQ in `ARCHIVING` with `ctx={"intent_issue_id": "intent-1"}`
- **WHEN** `engine.step` runs with `event=Event.ARCHIVE_DONE`
- **THEN** `intent_status.patch_terminal_status` MUST be invoked with
  `intent_issue_id="intent-1"`, `terminal_state=ReqState.DONE`
- **AND** the helper invocation MUST happen synchronously (awaited) before
  the transition's action dispatch (no action for ARCHIVE_DONE means it
  also runs before `step` returns)

#### Scenario: BIS-S10 PR_CI_RUNNING + PR_CI_TIMEOUT → ESCALATED PATCHes intent statusId="review"

- **GIVEN** a REQ in `PR_CI_RUNNING` with `ctx={"intent_issue_id": "intent-2"}`
- **WHEN** `engine.step` runs with `event=Event.PR_CI_TIMEOUT`
- **THEN** `intent_status.patch_terminal_status` MUST be invoked with
  `intent_issue_id="intent-2"`, `terminal_state=ReqState.ESCALATED`

### Requirement: escalate SESSION_FAILED inner CAS MUST sync intent statusId

The escalate action SHALL await an `intent_status.patch_terminal_status`
call immediately after the inner `req_state.cas_transition` that pushes
the REQ from a `*_RUNNING` self-loop into `ESCALATED` succeeds. Because
the engine treats the SESSION_FAILED self-loop as a non-terminal transition,
its own terminal hook does not fire for this path, so the escalate handler
MUST propagate the terminal statusId to BKD on its own. The PATCH MUST
happen exactly once per real-escalate invocation.

#### Scenario: BIS-S11 SESSION_FAILED retry exhausted → escalate writes statusId="review"

- **GIVEN** `escalate` invoked with `body.event="session.failed"`,
  `ctx.auto_retry_count=2` (retry exhausted), no PR-merged override
  applies
- **WHEN** the action's inner CAS to `ReqState.ESCALATED` succeeds
- **THEN** `intent_status.patch_terminal_status` MUST be invoked with
  `terminal_state=ReqState.ESCALATED` and the resolved
  `intent_issue_id` (from `ctx.intent_issue_id` or `body.issueId`)
- **AND** the helper MUST be invoked exactly once per `escalate` call
- **AND** any PATCH failure MUST NOT prevent the rest of escalate from
  completing (cleanup_runner, log warning, return)

### Requirement: PR-merged override MUST keep its current done PATCH unchanged

The PR-merged shortcut SHALL continue to PATCH the BKD intent issue via a
single `merge_tags_and_update` call that bundles
`add=["done", "via:pr-merge"]` together with `status_id="done"` — the new
`intent_status` helper MUST NOT replace, duplicate, or otherwise interfere
with that bundled PATCH. The override path is solely responsible for its
own statusId write because bundling the tag and status changes into one
BKD PATCH SHALL remain the canonical form for that path.

#### Scenario: BIS-S12 PR-merged override path keeps single BKD merge_tags_and_update call

- **GIVEN** an escalate invocation that triggers
  `_apply_pr_merged_done_override` (all involved-repo PRs merged)
- **WHEN** the override CAS to `DONE` succeeds
- **THEN** `bkd.merge_tags_and_update` MUST be called exactly once with
  `add` containing both `"done"` and `"via:pr-merge"` AND
  `status_id="done"`
- **AND** the override path MUST NOT additionally invoke
  `intent_status.patch_terminal_status` for that same inner CAS
- **AND** the engine's terminal hook (if it ran earlier with
  `terminal_state=ESCALATED`) MUST be allowed to write `status_id="review"`
  first; the override's later `status_id="done"` PATCH is the final word
  because escalate's await ordering guarantees override runs after engine
  hook
