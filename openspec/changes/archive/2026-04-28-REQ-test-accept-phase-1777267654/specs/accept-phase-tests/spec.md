## ADDED Requirements

### Requirement: Engine.step routes ACCEPT_RUNNING + ACCEPT_PASS to teardown then archive

The state-machine engine (`orchestrator/src/orchestrator/engine.py::step`) SHALL
advance a row from `ACCEPT_RUNNING` to `ACCEPT_TEARING_DOWN` when the
`accept.pass` event arrives, and MUST dispatch the `teardown_accept_env`
action exactly once. When the action's return value carries
`{"emit": "teardown-done.pass"}` the engine MUST chain into
`(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING`, dispatching
`done_archive` once. Neither transition leg crosses a terminal state, so the
engine MUST NOT schedule `cleanup_runner` along this happy-path.

#### Scenario: APT-S1 accept pass advances through teardown to archiving

- **GIVEN** a FakePool row at state `ACCEPT_RUNNING`, an injected fake
  `k8s_runner` controller, and stub actions where `teardown_accept_env`
  returns `{"emit": "teardown-done.pass"}` and `done_archive` returns
  `{"ok": True}`
- **WHEN** `engine.step` runs at `(ACCEPT_RUNNING, ACCEPT_PASS)`
- **THEN** the call MUST NOT raise; the returned dict MUST contain
  `action="teardown_accept_env"` and a `chained` dict whose `action` is
  `"done_archive"` and whose `next_state` equals `"archiving"`; the row
  state MUST be `ARCHIVING`; both stubs MUST have been invoked exactly once;
  the fake controller's `cleanup_runner` mock MUST NOT have been awaited

### Requirement: Engine.step routes ACCEPT_RUNNING + ACCEPT_FAIL through teardown to verifier

The engine SHALL advance a row from `ACCEPT_RUNNING` to
`ACCEPT_TEARING_DOWN` when the `accept.fail` event arrives, and MUST
dispatch the same `teardown_accept_env` action — `accept.pass` and
`accept.fail` share the teardown step because env-down is mandatory before
the verifier sees the failure. When the action emits `teardown-done.fail`
the engine MUST chain into `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) →
REVIEW_RUNNING`, dispatching `invoke_verifier_for_accept_fail` once. The
chain MUST NOT trigger `cleanup_runner` because `REVIEW_RUNNING` is not a
terminal state.

#### Scenario: APT-S2 accept fail advances through teardown to verifier

- **GIVEN** a FakePool row at state `ACCEPT_RUNNING`, an injected fake
  `k8s_runner` controller, and stub actions where `teardown_accept_env`
  returns `{"emit": "teardown-done.fail"}` and
  `invoke_verifier_for_accept_fail` returns `{"ok": True}`
- **WHEN** `engine.step` runs at `(ACCEPT_RUNNING, ACCEPT_FAIL)`
- **THEN** the call MUST NOT raise; the returned dict MUST contain
  `action="teardown_accept_env"` and a `chained` dict whose `action` is
  `"invoke_verifier_for_accept_fail"` and whose `next_state` equals
  `"review-running"`; the row state MUST be `REVIEW_RUNNING`; both stubs
  MUST have been invoked exactly once; `cleanup_runner` MUST NOT have been
  awaited

### Requirement: Engine.step escalates ACCEPT_ENV_UP_FAIL with cleanup retain_pvc

The engine SHALL transition `(ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) → ESCALATED`
and MUST dispatch the `escalate` action. Because the destination is a
terminal state and the source is non-terminal, the engine MUST schedule
`cleanup_runner(req_id, retain_pvc=True)` exactly once via
`asyncio.create_task` —  PVC retention is mandatory on `ESCALATED` so a
human can debug the failed lab.

#### Scenario: APT-S3 accept env-up fail escalates and triggers cleanup

- **GIVEN** a FakePool row at state `ACCEPT_RUNNING`, an injected fake
  `k8s_runner` controller, and a stub `escalate` action returning
  `{"escalated": True}`
- **WHEN** `engine.step` runs at `(ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL)` and
  background tasks are drained
- **THEN** the call MUST NOT raise; the returned dict MUST contain
  `action="escalate"` and `next_state="escalated"`; the row state MUST be
  `ESCALATED`; the stub `escalate` MUST have been invoked exactly once; the
  fake controller's `cleanup_runner` mock MUST have been awaited exactly
  once with `("REQ-1", retain_pvc=True)`

### Requirement: Engine.step routes ACCEPT_TEARING_DOWN + TEARDOWN_DONE_PASS to archiving

The engine SHALL transition `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) →
ARCHIVING` and MUST dispatch `done_archive` exactly once. When invoked
without a chained emit, the engine MUST NOT schedule `cleanup_runner`
because `ARCHIVING` is not a terminal state — cleanup happens on the next
hop into `DONE`, not here.

#### Scenario: APT-S4 teardown-done.pass advances to archiving

- **GIVEN** a FakePool row at state `ACCEPT_TEARING_DOWN`, an injected fake
  `k8s_runner` controller, and a stub `done_archive` returning
  `{"ok": True}`
- **WHEN** `engine.step` runs at `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)`
  and background tasks are drained
- **THEN** the call MUST NOT raise; the returned dict MUST contain
  `action="done_archive"` and `next_state="archiving"`; the row state MUST
  be `ARCHIVING`; the stub MUST have been invoked exactly once;
  `cleanup_runner` MUST NOT have been awaited

### Requirement: Engine.step routes ACCEPT_TEARING_DOWN + TEARDOWN_DONE_FAIL to verifier

The engine SHALL transition `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) →
REVIEW_RUNNING` and MUST dispatch `invoke_verifier_for_accept_fail` exactly
once. The transition MUST NOT trigger `cleanup_runner` because
`REVIEW_RUNNING` is not a terminal state.

#### Scenario: APT-S5 teardown-done.fail routes to verifier

- **GIVEN** a FakePool row at state `ACCEPT_TEARING_DOWN`, an injected fake
  `k8s_runner` controller, and a stub `invoke_verifier_for_accept_fail`
  returning `{"ok": True}`
- **WHEN** `engine.step` runs at `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL)`
  and background tasks are drained
- **THEN** the call MUST NOT raise; the returned dict MUST contain
  `action="invoke_verifier_for_accept_fail"` and
  `next_state="review-running"`; the row state MUST be `REVIEW_RUNNING`;
  the stub MUST have been invoked exactly once; `cleanup_runner` MUST NOT
  have been awaited

### Requirement: Engine.step routes ACCEPT_RUNNING + SESSION_FAILED as self-loop

The engine SHALL treat `(ACCEPT_RUNNING, SESSION_FAILED)` as a self-loop
whose `next_state` equals `cur_state`. The transition MUST dispatch the
`escalate` action exactly once — the action itself decides between
auto-resume and real escalate based on `ctx.auto_retry_count`. Because both
endpoints are non-terminal, the engine MUST NOT schedule `cleanup_runner`.

#### Scenario: APT-S6 session.failed at accept-running keeps state and dispatches escalate

- **GIVEN** a FakePool row at state `ACCEPT_RUNNING`, an injected fake
  `k8s_runner` controller, and a stub `escalate` returning `{"ok": True}`
- **WHEN** `engine.step` runs at `(ACCEPT_RUNNING, SESSION_FAILED)` and
  background tasks are drained
- **THEN** the call MUST NOT raise; the returned dict MUST contain
  `action="escalate"` and `next_state="accept-running"`; the row state
  MUST remain `ACCEPT_RUNNING`; the stub MUST have been invoked exactly
  once; `cleanup_runner` MUST NOT have been awaited

### Requirement: Engine.step routes ACCEPT_TEARING_DOWN + SESSION_FAILED as self-loop

The engine SHALL treat `(ACCEPT_TEARING_DOWN, SESSION_FAILED)` as a
self-loop. The transition MUST dispatch the `escalate` action exactly once
and MUST keep the row state at `ACCEPT_TEARING_DOWN`. Because both
endpoints are non-terminal the engine MUST NOT schedule `cleanup_runner`.

#### Scenario: APT-S7 session.failed at accept-tearing-down keeps state and dispatches escalate

- **GIVEN** a FakePool row at state `ACCEPT_TEARING_DOWN`, an injected fake
  `k8s_runner` controller, and a stub `escalate` returning `{"ok": True}`
- **WHEN** `engine.step` runs at `(ACCEPT_TEARING_DOWN, SESSION_FAILED)`
  and background tasks are drained
- **THEN** the call MUST NOT raise; the returned dict MUST contain
  `action="escalate"` and `next_state="accept-tearing-down"`; the row
  state MUST remain `ACCEPT_TEARING_DOWN`; the stub MUST have been invoked
  exactly once; `cleanup_runner` MUST NOT have been awaited
