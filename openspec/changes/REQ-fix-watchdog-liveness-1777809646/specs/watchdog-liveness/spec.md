# Spec Delta — watchdog liveness check

## ADDED Requirements

### Requirement: Watchdog suppresses escalate when BKD logs show recent activity

The orchestrator watchdog SHALL probe the BKD `/issues/{id}/logs` endpoint for
the latest log entry timestamp before deciding to escalate a stuck stage. If
that timestamp is within `watchdog_liveness_grace_sec` (default 120 seconds),
the watchdog MUST treat the stage as live and return without firing
`SESSION_FAILED`, regardless of the BKD `sessionStatus` value. This applies to
both the ended-session fast lane and the long-tail slow lane in
`_check_and_escalate`. The check MUST NOT bypass the explicit
`fixer-round-cap` hard-stop branch, because that branch is a deterministic
upper bound that does not depend on agent liveness.

#### Scenario: WLC-S1 fresh activity within grace skips escalate
- **GIVEN** a REQ in `ANALYZING` whose BKD intent issue has `sessionStatus="completed"` and the latest log entry `createdAt` is 30 seconds before now
- **WHEN** `watchdog._check_and_escalate` runs against that row with `stuck_sec=400` (above the stage `ended_sec=300`)
- **THEN** the function returns `False`, no `SESSION_FAILED` event is emitted, and a `watchdog.live_activity_skip` debug log is recorded

#### Scenario: WLC-S2 stale activity falls through to escalate
- **GIVEN** a REQ in `SPEC_LINT_RUNNING` whose BKD issue has `sessionStatus="completed"` and the latest log entry `createdAt` is 600 seconds before now
- **WHEN** `watchdog._check_and_escalate` runs with `stuck_sec=400` (above `ended_sec=300`)
- **THEN** the function proceeds to `engine.step` with `Event.SESSION_FAILED` exactly as before the activity check existed

#### Scenario: WLC-S3 fixer-round-cap still escalates regardless of activity
- **GIVEN** a REQ in `FIXER_RUNNING` with `ctx.fixer_round` >= `settings.fixer_round_cap` and the latest log entry `createdAt` 5 seconds before now
- **WHEN** `watchdog._check_and_escalate` runs
- **THEN** the function still emits `SESSION_FAILED` with `escalated_reason="fixer-round-cap"` (activity check MUST NOT short-circuit the hard cap)

### Requirement: BKD client exposes a lightweight last-activity probe

`BKDRestClient` SHALL expose `async def last_log_activity_at(project_id, issue_id) -> datetime | None`
that performs a single `GET /projects/{pid}/issues/{iid}/logs?limit=10`,
parses the maximum `createdAt` timestamp across the returned entries, and
returns it as a timezone-aware `datetime` in UTC. The method MUST return
`None` when the response has no `logs`, when an entry's `createdAt` is missing
or unparseable, or when the HTTP call raises. The method SHALL NOT raise; all
errors MUST be swallowed and logged at warning level so the watchdog caller
can fall back to existing behaviour.

#### Scenario: WLC-S4 returns max createdAt across log entries
- **GIVEN** BKD `/logs?limit=10` returns three entries with `createdAt` timestamps `T-300s`, `T-30s`, `T-90s`
- **WHEN** `last_log_activity_at` is called
- **THEN** the returned `datetime` equals `T-30s` parsed in UTC

#### Scenario: WLC-S5 returns None on empty logs
- **GIVEN** BKD `/logs?limit=10` returns `{"logs": []}`
- **WHEN** `last_log_activity_at` is called
- **THEN** the function returns `None`

#### Scenario: WLC-S6 swallows HTTP errors and returns None
- **GIVEN** the BKD `/logs` call raises an exception
- **WHEN** `last_log_activity_at` is called
- **THEN** the function returns `None` and logs `bkd.last_log_activity_at.failed` at warning level

### Requirement: Auto-resume path leaves parent intent issue tags untouched

The `escalate` action SHALL keep the BKD intent issue tag set unchanged when
it takes the auto-resume branch (transient signal plus `auto_retry_count <
_MAX_AUTO_RETRY`). The action MUST only invoke `bkd.follow_up_issue` on the
failed issue and update `req_state.context` with the new `auto_retry_count`
and `last_retry_reason`. This invariant lets long analyze sessions that are
auto-resumed remain visually clean in BKD UI and keeps
`verifier_decisions.actual_outcome` backfill from incorrectly classifying
recovered REQs as escalated.

#### Scenario: WLC-S7 transient escalate with retry slack does not tag intent issue
- **GIVEN** a `body.event="watchdog.stuck"` escalate call with `ctx.auto_retry_count=0` and `_MAX_AUTO_RETRY=2`
- **WHEN** the `escalate` action is invoked
- **THEN** `BKDClient.merge_tags_and_update` is NOT called for the intent issue with `escalated` in the `add` list, and the action returns `{"auto_resumed": True, "retry": 1, ...}`
