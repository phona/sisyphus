## ADDED Requirements

### Requirement: Background TTL cleanup task removes stale rows from append-only tables

The orchestrator SHALL run a background asyncio task (`table_ttl.run_loop`) that
periodically invokes `run_ttl_cleanup(pool)` to delete rows older than configured
retention thresholds from four append-only tables. The task MUST be started during
application startup when `ttl_cleanup_enabled=True` and
`ttl_cleanup_interval_sec > 0`. When `ttl_cleanup_enabled=False` the loop MUST
return immediately without performing any deletions.

#### Scenario: TTL-S1 event_seen rows older than ttl_event_seen_days are deleted
- **GIVEN** event_seen contains a row with seen_at 40 days ago and another with seen_at 1 day ago
- **WHEN** run_ttl_cleanup is called with ttl_event_seen_days=30
- **THEN** the 40-day-old row is deleted and the 1-day-old row is retained

#### Scenario: TTL-S2 dispatch_slugs rows older than ttl_dispatch_slugs_days are deleted
- **GIVEN** dispatch_slugs contains a row with created_at older than ttl_dispatch_slugs_days
- **WHEN** run_ttl_cleanup is invoked
- **THEN** the DELETE SQL targets dispatch_slugs WHERE created_at < cutoff

#### Scenario: TTL-S3 verifier_decisions rows older than ttl_verifier_decisions_days are deleted
- **GIVEN** verifier_decisions contains a row with made_at older than ttl_verifier_decisions_days
- **WHEN** run_ttl_cleanup is invoked
- **THEN** the DELETE SQL targets verifier_decisions WHERE made_at < cutoff

#### Scenario: TTL-S4 run_ttl_cleanup returns summary dict with before/after/deleted per table
- **GIVEN** run_ttl_cleanup completes successfully
- **WHEN** the return value is inspected
- **THEN** result contains keys event_seen, dispatch_slugs, verifier_decisions, stage_runs,
  each with sub-keys before (int), after (int), deleted (int = before - after)

### Requirement: stage_runs rows with ended_at IS NULL are never deleted

The cleanup task MUST NOT delete any `stage_runs` row where `ended_at IS NULL`.
Such rows represent still-running or abnormally-terminated stage agents that may
be needed for incident debugging. Only closed rows (ended_at IS NOT NULL) older
than `ttl_stage_runs_closed_days` SHALL be deleted.

#### Scenario: TTL-S5 open stage_runs (ended_at IS NULL) are never deleted
- **GIVEN** a stage_runs row with started_at 180 days ago and ended_at IS NULL
- **WHEN** run_ttl_cleanup is called with ttl_stage_runs_closed_days=1
- **THEN** the row still exists in the database after cleanup

#### Scenario: TTL-S6 closed stage_runs older than ttl_stage_runs_closed_days are deleted
- **GIVEN** stage_runs contains a row with ended_at 95 days ago and one with ended_at 1 day ago
- **WHEN** run_ttl_cleanup is called with ttl_stage_runs_closed_days=90
- **THEN** the 95-day-old closed row is deleted and the 1-day-old closed row is retained

#### Scenario: TTL-S7 stage_runs DELETE SQL includes ended_at IS NOT NULL guard
- **GIVEN** run_ttl_cleanup is invoked
- **WHEN** the DELETE statement for stage_runs is inspected
- **THEN** the SQL contains both "ended_at IS NOT NULL" AND "ended_at < $1"

### Requirement: TTL thresholds and loop interval are configurable via environment variables

The orchestrator SHALL read TTL configuration from the following environment
variables (all prefixed `SISYPHUS_`). The system MUST apply conservative defaults
so that deployments without explicit configuration retain data longer rather than
deleting it too aggressively.

Defaults:
- `SISYPHUS_TTL_CLEANUP_ENABLED`: `true`
- `SISYPHUS_TTL_CLEANUP_INTERVAL_SEC`: `86400` (24 h)
- `SISYPHUS_TTL_EVENT_SEEN_DAYS`: `30`
- `SISYPHUS_TTL_DISPATCH_SLUGS_DAYS`: `90`
- `SISYPHUS_TTL_VERIFIER_DECISIONS_DAYS`: `90`
- `SISYPHUS_TTL_STAGE_RUNS_CLOSED_DAYS`: `90`

#### Scenario: TTL-S8 ttl_cleanup_enabled=False causes run_loop to return without blocking
- **GIVEN** SISYPHUS_TTL_CLEANUP_ENABLED=false
- **WHEN** run_loop() is awaited
- **THEN** the coroutine returns immediately without entering the while loop

#### Scenario: TTL-S9 startup spawns table_ttl background task when enabled
- **GIVEN** SISYPHUS_TTL_CLEANUP_ENABLED=true and SISYPHUS_TTL_CLEANUP_INTERVAL_SEC > 0
- **WHEN** orchestrator startup() runs
- **THEN** a background asyncio.Task named "table_ttl" is added to the _bg_tasks list
