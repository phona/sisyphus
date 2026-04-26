DROP INDEX IF EXISTS idx_stage_runs_bkd_session;
ALTER TABLE stage_runs DROP COLUMN IF EXISTS bkd_session_id;
