# watchdog-fast-detection

## ADDED Requirements

### Requirement: Watchdog SQL prefilter MUST use the smaller of the fast and slow stuck thresholds

The watchdog `_tick` SQL query SHALL prefilter on
`updated_at < NOW() - INTERVAL '1 second' * T` where `T` is
`min(settings.watchdog_session_ended_threshold_sec,
settings.watchdog_stuck_threshold_sec)`. The watchdog MUST NOT inline
either threshold value as a hard-coded constant. The SQL MUST continue
to exclude rows whose `state` is in the existing `_SKIP_STATES` set
(`done`, `escalated`, `gh-incident-open`, `init`).

#### Scenario: WFD-S1 fast threshold smaller than slow threshold drives SQL filter

- **GIVEN** `settings.watchdog_session_ended_threshold_sec = 300`
- **AND** `settings.watchdog_stuck_threshold_sec = 3600`
- **WHEN** `watchdog._tick()` issues its `SELECT` against the pool
- **THEN** the second positional parameter passed to `pool.fetch` MUST be
  `300`, not `3600`
- **AND** the first positional parameter MUST be a list containing
  `done`, `escalated`, `gh-incident-open`, and `init`

#### Scenario: WFD-S2 slow threshold smaller than fast threshold (operator override) drives SQL filter

- **GIVEN** `settings.watchdog_session_ended_threshold_sec = 1800`
- **AND** `settings.watchdog_stuck_threshold_sec = 600`
- **WHEN** `watchdog._tick()` issues its `SELECT`
- **THEN** the threshold parameter MUST be `600`

### Requirement: Watchdog MUST escalate ended sessions once stuck_sec reaches the fast threshold

The watchdog MUST treat any row whose BKD issue has `session_status` not
equal to `"running"` (i.e., the session ended without a webhook reaching
sisyphus) as eligible for escalation as soon as
`stuck_sec >= settings.watchdog_session_ended_threshold_sec`.
The watchdog SHALL NOT require `stuck_sec` to also exceed
`settings.watchdog_stuck_threshold_sec` for ended sessions.

The path MUST insert an `artifact_checks` row with `stage =
"watchdog:<state>"` and call `engine.step` with `event = Event.SESSION_FAILED`
and a synthetic `body.event` of `"watchdog.stuck"` (or `"archive.failed"`
for `ARCHIVING` state, or `"watchdog.intake_no_result_tag"` for the
existing intake-no-result-tag branch). All existing fault-isolation,
fixer-round-cap detection, and intake-no-result-tag detection MUST
remain unchanged.

#### Scenario: WFD-S3 ended session at 305s escalates without waiting 3600s

- **GIVEN** a row with `state = analyzing`, `stuck_sec = 305`,
  `ctx.intent_issue_id = "intent-1"`, and BKD reports
  `session_status = "failed"` for that issue
- **AND** `settings.watchdog_session_ended_threshold_sec = 300`
- **AND** `settings.watchdog_stuck_threshold_sec = 3600`
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST be called once with
  `event == Event.SESSION_FAILED` and `body.event == "watchdog.stuck"`
- **AND** an `artifact_checks` row MUST be inserted with
  `stage == "watchdog:analyzing"`
- **AND** the tick result MUST be `{"checked": 1, "escalated": 1}`

#### Scenario: WFD-S4 ended session at 200s is filtered out at SQL level

- **GIVEN** the SQL prefilter uses fast threshold = 300s
- **AND** a row whose `updated_at` is 200s in the past
- **WHEN** `watchdog._tick()` runs
- **THEN** the row MUST NOT be returned by `pool.fetch` (database-side
  filter); behaviour is enforced by the SQL itself, not by Python

### Requirement: Watchdog MUST continue to skip sessions reported as running regardless of stuck_sec

The watchdog MUST skip any row whose BKD issue has
`session_status == "running"`. The skip MUST NOT depend on the value of
`stuck_sec`, and MUST hold whether `stuck_sec` is just above the fast
threshold or far above the slow threshold. The skip MUST emit a
`watchdog.still_running` debug log line and return without writing
`artifact_checks` or invoking `engine.step`.

#### Scenario: WFD-S5 running session at 5000s remains skipped

- **GIVEN** a row with `state = analyzing`, `stuck_sec = 5000`, BKD
  reports `session_status = "running"`
- **AND** `settings.watchdog_session_ended_threshold_sec = 300`
- **AND** `settings.watchdog_stuck_threshold_sec = 3600`
- **WHEN** `watchdog._tick()` runs
- **THEN** `engine.step` MUST NOT be called
- **AND** `artifact_checks.insert_check` MUST NOT be called
- **AND** the tick result MUST be `{"checked": 1, "escalated": 0}`

#### Scenario: WFD-S6 running session at 305s remains skipped

- **GIVEN** a row with `state = analyzing`, `stuck_sec = 305`, BKD
  reports `session_status = "running"`
- **WHEN** `watchdog._tick()` runs
- **THEN** the tick result MUST be `{"checked": 1, "escalated": 0}`

### Requirement: Settings MUST expose the fast threshold via env var

`orchestrator.config.Settings` MUST add a field
`watchdog_session_ended_threshold_sec` of type `int` with default `300`.
The field MUST follow the existing `env_prefix = "SISYPHUS_"` convention
so operators can override it via the
`SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC` environment variable
without any helm chart change. The existing
`watchdog_stuck_threshold_sec` field and its `3600` default MUST be
preserved.

#### Scenario: WFD-S7 default value is 300s

- **GIVEN** an orchestrator process started with no
  `SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC` env var set
- **WHEN** the `Settings` instance is constructed
- **THEN** `settings.watchdog_session_ended_threshold_sec` MUST equal `300`

#### Scenario: WFD-S8 env var override is respected

- **GIVEN** the env var
  `SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC=120` is set
- **WHEN** the `Settings` instance is constructed
- **THEN** `settings.watchdog_session_ended_threshold_sec` MUST equal `120`
