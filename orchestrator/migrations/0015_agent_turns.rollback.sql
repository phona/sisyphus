DROP INDEX IF EXISTS idx_agent_turns_req;
DROP TABLE IF EXISTS agent_turns;
DROP INDEX IF EXISTS idx_stage_runs_bkd_issue;
ALTER TABLE stage_runs DROP COLUMN IF EXISTS bkd_issue_id;
