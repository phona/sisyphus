-- Rollback for 0009_artifact_checks_flake.sql

DROP INDEX IF EXISTS idx_artifact_checks_flake_reason;

ALTER TABLE artifact_checks
    DROP COLUMN IF EXISTS flake_reason;

ALTER TABLE artifact_checks
    DROP COLUMN IF EXISTS attempts;
