## ADDED Requirements

### Requirement: watchdog SHALL apply per-stage typed policy via STAGE_WATCHDOG_POLICY table

The watchdog MUST resolve its escalation behavior from a per-stage policy
table `orchestrator.watchdog._STAGE_POLICY: dict[ReqState, _StagePolicy | None]`.
Each entry has the value semantics:

- `None` — the state is fully exempt; SHALL be unioned into the SQL pre-filter
  skip list so rows in this state are never returned by `pool.fetch()` and
  never enter `_check_and_escalate()`. Used for `human-loop-conversation`
  stages (currently only `INTAKING`).
- `_StagePolicy(ended_sec=int, stuck_sec=int | None)` — per-stage thresholds:
  * `ended_sec` MUST be applied when BKD reports `session_status` not in
    `{"running"}` (or when no BKD issue is associated with the state). If
    `stuck_sec_actual >= ended_sec`, the watchdog MUST escalate via
    `engine.step` with `event=SESSION_FAILED`.
  * `stuck_sec` MUST be applied when BKD reports `session_status == "running"`.
    If `stuck_sec` is `None`, the watchdog MUST skip the row regardless of
    duration (preserves the existing "do not kill long-tail running session"
    semantics). If `stuck_sec` is an `int` and `stuck_sec_actual >= stuck_sec`,
    the watchdog MUST escalate.

States NOT present in `_STAGE_POLICY` MUST fall back to a synthesized policy
of `_StagePolicy(ended_sec=settings.watchdog_session_ended_threshold_sec,
stuck_sec=settings.watchdog_stuck_threshold_sec)`. This guarantees that adding
a new `ReqState` without updating `_STAGE_POLICY` does not silently disable
watchdog coverage for that state.

`orchestrator.watchdog._NO_WATCHDOG_STATES` MUST be derived from `_STAGE_POLICY`
as `frozenset(s for s, p in _STAGE_POLICY.items() if p is None)`. Other modules
that import this set continue to work unchanged.

#### Scenario: WSPF-S1 INTAKING (human-loop) excluded by SQL pre-filter

- **GIVEN** `_STAGE_POLICY[ReqState.INTAKING]` is `None`
- **WHEN** `watchdog._tick()` runs
- **THEN** the first SQL parameter array passed to `pool.fetch()` MUST contain
  the literal string `"intaking"`
- **AND** `ReqState.INTAKING` MUST be a member of `watchdog._NO_WATCHDOG_STATES`

#### Scenario: WSPF-S2 deterministic-checker stage with ended session escalates after ended_sec

- **GIVEN** a REQ in state `SPEC_LINT_RUNNING` with `stuck_sec_actual=400`
- **AND** `_STAGE_POLICY[ReqState.SPEC_LINT_RUNNING].ended_sec == 300`
- **AND** the state has no associated BKD issue (`_STATE_ISSUE_KEY[SPEC_LINT_RUNNING] is None`)
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST be called once with `event=SESSION_FAILED` and
  `cur_state=ReqState.SPEC_LINT_RUNNING`
- **AND** `body.event` MUST be `"watchdog.stuck"`

#### Scenario: WSPF-S3 autonomous-bounded stage running indefinitely is NOT escalated (stuck_sec=None)

- **GIVEN** a REQ in state `ANALYZING` with `stuck_sec_actual=10000` (~3h)
- **AND** `_STAGE_POLICY[ReqState.ANALYZING].stuck_sec is None`
- **AND** BKD reports `session_status="running"`
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST NOT be called for this row
- **AND** the row MUST NOT produce an `artifact_checks` insert

#### Scenario: WSPF-S4 autonomous-bounded stage with ended session escalates after ended_sec

- **GIVEN** a REQ in state `ANALYZING` with `stuck_sec_actual=320`
- **AND** `_STAGE_POLICY[ReqState.ANALYZING].ended_sec == 300`
- **AND** BKD reports `session_status="failed"`
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST be called once with `event=SESSION_FAILED` and
  `cur_state=ReqState.ANALYZING`

#### Scenario: WSPF-S5 external-poll stage running long but under stuck_sec is NOT escalated

- **GIVEN** a REQ in state `PR_CI_RUNNING` with `stuck_sec_actual=3600` (1h)
- **AND** `_STAGE_POLICY[ReqState.PR_CI_RUNNING].stuck_sec == 14400` (4h)
- **AND** BKD reports `session_status="running"`
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST NOT be called for this row

#### Scenario: WSPF-S6 external-poll stage running over stuck_sec is escalated

- **GIVEN** a REQ in state `PR_CI_RUNNING` with `stuck_sec_actual=15000` (~4.2h)
- **AND** `_STAGE_POLICY[ReqState.PR_CI_RUNNING].stuck_sec == 14400`
- **AND** BKD reports `session_status="running"`
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST be called once with `event=SESSION_FAILED` and
  `cur_state=ReqState.PR_CI_RUNNING`
- **AND** `body.event` MUST be `"watchdog.stuck"`

#### Scenario: WSPF-S7 unmapped state falls back to global watchdog thresholds

- **GIVEN** a hypothetical `ReqState` value `"new-future-stage"` not present in
  `_STAGE_POLICY` AND not in `_SKIP_STATES`
- **AND** `settings.watchdog_session_ended_threshold_sec == 300` and
  `settings.watchdog_stuck_threshold_sec == 3600`
- **WHEN** `_check_and_escalate()` resolves a row with this state
- **THEN** the resolved policy MUST equal `_StagePolicy(ended_sec=300, stuck_sec=3600)`

#### Scenario: WSPF-S8 SQL pre-filter threshold is min over all configured policy windows

- **GIVEN** `_STAGE_POLICY` contains at least one policy with `ended_sec=300`
- **WHEN** `watchdog._tick()` runs and we capture the second SQL parameter
  (the threshold)
- **THEN** the threshold MUST be `<= 300` to ensure no candidate row is
  silently filtered out before per-row policy evaluation

### Requirement: STAGING_TEST_RUNNING SHALL not kill long-running test suites by slow-lane

The `_STAGE_POLICY[ReqState.STAGING_TEST_RUNNING]` entry MUST set `stuck_sec=None`
even though the state is technically a `deterministic-checker`. The watchdog
SHALL NOT escalate a `STAGING_TEST_RUNNING` row whose BKD session is `running`,
regardless of how long it has been in that state. This preserves the prior
contract that running BKD sessions for staging tests (which can include slow
unit/integration suites) are not killed by watchdog timeouts.

#### Scenario: WSPF-S3b STAGING_TEST_RUNNING running session is NOT escalated regardless of duration

- **GIVEN** a REQ in state `STAGING_TEST_RUNNING` with `stuck_sec_actual=20000`
- **AND** `_STAGE_POLICY[ReqState.STAGING_TEST_RUNNING].stuck_sec is None`
- **AND** BKD reports `session_status="running"`
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST NOT be called for this row
