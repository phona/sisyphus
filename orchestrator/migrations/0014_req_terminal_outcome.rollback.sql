DROP INDEX IF EXISTS idx_req_state_terminal_outcome;
ALTER TABLE req_state DROP COLUMN IF EXISTS terminal_outcome;
