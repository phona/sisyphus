## ADDED Requirements

### Requirement: orchestrator startup automatically applies observability schema

The system SHALL apply `observability/schema.sql` to the observability database during orchestrator startup, after the obs pool is initialized and before background tasks begin. The application MUST be idempotent and MUST NOT block main service startup on failure.

#### Scenario: OBS-S1 applies schema on startup when obs pool is configured
- **GIVEN** `SISYPHUS_OBS_PG_DSN` is set to a valid PostgreSQL DSN
- **WHEN** orchestrator startup runs
- **THEN** `observability/schema.sql` is executed against the obs database

#### Scenario: OBS-S2 skips schema apply when obs pool is not configured
- **GIVEN** `SISYPHUS_OBS_PG_DSN` is empty or unset
- **WHEN** orchestrator startup runs
- **THEN** schema apply is skipped with an info log and startup continues normally

#### Scenario: OBS-S3 skips schema apply when schema file is missing
- **GIVEN** obs pool is configured and the resolved schema file path does not exist
- **WHEN** `apply_obs_schema()` is called
- **THEN** it logs a warning, returns True, and does not throw

#### Scenario: OBS-S4 does not block startup on schema apply failure
- **GIVEN** obs pool is configured and schema file exists
- **WHEN** the database execute fails (e.g., PG is unreachable)
- **THEN** `apply_obs_schema()` logs a warning, returns False, and orchestrator startup continues

#### Scenario: OBS-S5 schema application is idempotent
- **GIVEN** the observability schema has already been applied
- **WHEN** orchestrator restarts and `apply_obs_schema()` runs again
- **THEN** it succeeds without error because schema.sql uses `IF NOT EXISTS` and `OR REPLACE`

#### Scenario: OBS-S6 supports env override for schema file path
- **GIVEN** `SISYPHUS_OBS_SCHEMA_PATH` is set to a custom file path
- **WHEN** `_resolve_schema_path()` is called
- **THEN** it returns the path specified by the environment variable
