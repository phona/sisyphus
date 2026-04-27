## ADDED Requirements

### Requirement: Engine.step rejects invalid emit strings without crashing

The state-machine engine (`orchestrator/src/orchestrator/engine.py::step`) SHALL
treat an action handler's return value `{"emit": "<unknown-string>"}` — where
`<unknown-string>` is not a member of the `Event` enum — as a no-op for the
chained recursion path. The engine MUST NOT raise; it MUST log
`engine.invalid_emit` and return the unchained `base_result` so the caller can
proceed safely. This guard exists because handlers are external code (BKD agent
prompts construct event names dynamically) and a typo in the emit name MUST NOT
take down the orchestrator.

#### Scenario: EAT-S1 unknown emit string is logged and dropped

- **GIVEN** an action handler registered for `start_challenger` returns
  `{"emit": "totally-not-an-event"}` from `engine.step` invoked at
  `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **WHEN** the engine processes the emit chain
- **THEN** `engine.step` MUST return without raising, the returned dict MUST
  contain `action="start_challenger"` and `next_state="challenger-running"`,
  and MUST NOT contain a `chained` key (the bogus emit was dropped)

### Requirement: Engine.step normalizes non-dict handler returns

`engine.step` MUST tolerate action handlers that return values other than
`dict` (e.g. `None`, lists, primitives). When the handler return value is not a
`dict`, the engine SHALL substitute an empty dict and skip the chained emit
path. The handler return value of any shape MUST NOT raise an `AttributeError`
or `TypeError` from `engine.step`. This requirement protects the engine from
adversarial / sloppy handler implementations and from `await` chains that
accidentally drop the return value.

#### Scenario: EAT-S2 handler returning None is treated as empty dict

- **GIVEN** an action handler registered for `start_challenger` returns `None`
- **WHEN** `engine.step` runs at `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **THEN** the call MUST NOT raise, the result MUST contain
  `action="start_challenger"` and `next_state="challenger-running"`, and the
  `result` field of the returned dict MUST be `{}` (the empty-dict
  substitution)

#### Scenario: EAT-S3 handler returning a list is treated as empty dict

- **GIVEN** an action handler registered for `start_challenger` returns
  `[1, 2, 3]`
- **WHEN** `engine.step` runs at `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **THEN** the call MUST NOT raise, and the returned dict MUST contain
  `action="start_challenger"` with no `chained` key

### Requirement: Engine.step survives row disappearance during emit chain

`engine.step` MUST early-return the parent `base_result` without raising
`AttributeError` when, after dispatching a chained action, the reloaded
`req_state.get` returns `None` (row deleted, DB reset, or test-mode purge
mid-chain). The chain MUST silently truncate at that point rather than
crashing the webhook handler. This guard exists because action handlers can
trigger DB compaction / admin cleanup as a side effect, and a missing row
mid-recursion is a recoverable race, not a programmer error.

#### Scenario: EAT-S4 row vanishes between dispatch and chained reload

- **GIVEN** an action handler registered for `create_spec_lint` returns
  `{"emit": "spec-lint.pass"}`, AND between the action's CAS-advance and the
  chained `req_state.get` the row for that REQ is removed from the FakePool
- **WHEN** `engine.step` runs at `(ANALYZE_ARTIFACT_CHECKING,
  ANALYZE_ARTIFACT_CHECK_PASS)`
- **THEN** the call MUST NOT raise, and the returned dict MUST contain
  `action="create_spec_lint"` without a `chained` key

### Requirement: Engine.step propagates illegal chained transitions as skip

`engine.step` MUST return `{"action": "skip", "reason": "no transition
<state>+<event>"}` when a handler emits an event that has no defined
transition from the post-dispatch state. The parent call MUST attach this
skip dict at `base_result["chained"]` rather than discarding it, so audit
logs preserve the dropped event for debugging.

#### Scenario: EAT-S5 chained emit hits illegal transition

- **GIVEN** an action handler registered for `start_challenger` returns
  `{"emit": "archive.done"}` (event has no transition from
  `CHALLENGER_RUNNING`)
- **WHEN** `engine.step` runs at `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)` so the
  CAS advances to `CHALLENGER_RUNNING` then chains
- **THEN** the returned dict MUST contain `action="start_challenger"`,
  `chained.action="skip"`, and `chained.reason` containing
  `"no transition challenger-running+archive.done"`

### Requirement: Engine.step reports unregistered action without crashing

`engine.step` MUST return `{"action": "error", "reason": "action <name> not
registered"}` without raising and without invoking the chain when the
transition table names an `action` string that is not present in
`actions.REGISTRY`. The CAS-advance to `next_state` has already been
committed at this point and MUST NOT be rolled back — terminal audit recovery
is the operator's job, not the engine's.

#### Scenario: EAT-S6 transition action missing from registry returns error

- **GIVEN** the `REGISTRY` has been cleared (no `start_challenger` registered)
- **WHEN** `engine.step` runs at `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **THEN** the returned dict MUST contain `action="error"`, the `reason` field
  MUST equal the literal string
  `"action start_challenger not registered"`, and the FakePool's row MUST
  have advanced to `state="challenger-running"` (CAS already committed)

### Requirement: Engine.step does not re-trigger runner cleanup on terminal self-loop

`engine.step` MUST trigger `cleanup_runner` exactly once when transitioning
**into** a terminal state (`DONE` / `ESCALATED`) **from** a non-terminal
state. Self-loop transitions where both `cur_state` and `next_state` are
terminal (e.g. `(ESCALATED, VERIFY_ESCALATE) → ESCALATED`, fired by a user
follow-up) MUST NOT schedule another cleanup task. This prevents cleanup
races where a just-resumed runner pod is killed by an idempotent self-loop
event.

#### Scenario: EAT-S7 ESCALATED + VERIFY_ESCALATE does not re-trigger cleanup

- **GIVEN** a row at state `ESCALATED` and a fake k8s controller injected
- **WHEN** `engine.step` runs at `(ESCALATED, VERIFY_ESCALATE)`
- **THEN** the returned dict MUST contain `action="no-op"`, the row state MUST
  remain `ESCALATED`, and the fake controller's `cleanup_runner` mock MUST NOT
  have been awaited

### Requirement: Engine.step is best-effort on stage_runs failures

`engine.step` MUST catch any database failure inside `_record_stage_transitions`
(connection error, schema drift, INSERT raising) and MUST log at WARNING level
(`engine.stage_runs.write_failed`) without propagating to the caller —
state-machine progress depends on CAS, not observability writes.

#### Scenario: EAT-S8 stage_runs insert raises but engine still advances

- **GIVEN** a FakePool whose `INSERT INTO stage_runs` path raises
  `RuntimeError("DB down")`
- **WHEN** `engine.step` runs at `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **THEN** the call MUST NOT raise, the returned dict MUST contain
  `action="start_challenger"`, and the row state MUST be `CHALLENGER_RUNNING`

### Requirement: Engine.step tolerates body objects missing optional attributes

`engine.step` MUST proceed with `issue_id=None` for observability records when
the webhook body lacks an `issueId` attribute, and a missing attribute MUST
NOT raise. The engine reads `body.issueId` via `getattr(body, "issueId",
None)` to support tests and edge cases where the webhook body is synthetic
(e.g. INTENT_ANALYZE injection from snapshot loop).

#### Scenario: EAT-S9 body without issueId attribute does not raise

- **GIVEN** a `body` object created with `type("B", (), {})()` (no attributes)
- **WHEN** `engine.step` runs at `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **THEN** the call MUST NOT raise, and the returned dict MUST contain
  `action="start_challenger"` and `next_state="challenger-running"`

### Requirement: Engine.step recursion guard fires at depth 13

`engine.step` MUST short-circuit and return `{"action": "error", "reason":
"engine recursion >12"}` without dispatching the action when `depth > 12`.
The engine accepts a `depth` parameter (default 0) and increments it on every
chained recursion; calls at exactly `depth=12` MUST still execute normally —
the boundary is strict greater-than.

#### Scenario: EAT-S10a depth=12 still dispatches handler

- **GIVEN** a registered handler `start_challenger`
- **WHEN** `engine.step` is invoked with `depth=12` at
  `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **THEN** the handler MUST be called once, and the returned dict MUST contain
  `action="start_challenger"` with no recursion-error reason

#### Scenario: EAT-S10b depth=13 triggers recursion guard

- **GIVEN** a registered handler `start_challenger`
- **WHEN** `engine.step` is invoked with `depth=13` at
  `(SPEC_LINT_RUNNING, SPEC_LINT_PASS)`
- **THEN** the handler MUST NOT be called, and the returned dict MUST equal
  `{"action": "error", "reason": "engine recursion >12"}`

### Requirement: Engine.step skips SESSION_FAILED on terminal states

`engine.step` MUST return `{"action": "skip", "reason": "no transition
<state>+session.failed"}` without invoking the `escalate` action when
`SESSION_FAILED` arrives at a terminal state (`DONE` or `ESCALATED`). The
transition table only registers `(SESSION_FAILED, *_RUNNING)` self-loops, so
terminal states have no `SESSION_FAILED` transition. This prevents zombie
session-failure events from re-escalating an already-archived REQ.

#### Scenario: EAT-S11 SESSION_FAILED on DONE is skipped

- **GIVEN** a row at state `DONE` and an `escalate` stub registered
- **WHEN** `engine.step` runs at `(DONE, SESSION_FAILED)`
- **THEN** the returned dict MUST contain `action="skip"` with reason
  starting `"no transition done+"`, and the `escalate` stub MUST NOT have
  been invoked

### Requirement: DONE terminal accepts no events

`engine.step(cur_state=DONE, ...)` MUST return `skip` for every member of the
`Event` enum without dispatching any action or scheduling cleanup. The `DONE`
state is a strict terminal state: every member of the `Event` enum MUST
yield `decide(DONE, event) is None`. This guards against late webhooks (BKD
`session.completed` arriving after archive) re-running work on an
already-finished REQ.

#### Scenario: EAT-S12 DONE skips every Event in the enum

- **GIVEN** a row at state `DONE`
- **WHEN** `engine.step` is invoked once for each member of `Event`
- **THEN** every call MUST return `action="skip"`, none of the calls MUST
  raise, and no action handler MUST be invoked across the full sweep
