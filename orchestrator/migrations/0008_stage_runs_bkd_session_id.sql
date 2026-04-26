-- 0008: stage_runs.bkd_session_id —— 把 BKD agent session token (Claude Code
-- externalSessionId) 落进 stage_runs，方便从指标看板直接跳到对应 BKD chat 排查
-- agent 行为，也让"哪条 prompt 该改"的反哺循环能 join agent_quality.sql 跟 BKD
-- session log。
--
-- 仅 agent stage（analyze/verifier/fixer/accept/archive）会有非 NULL 值；机械
-- checker（spec_lint/dev_cross_check/staging_test/pr_ci/accept_teardown）没 BKD
-- 会话，列保留 NULL。

ALTER TABLE stage_runs ADD COLUMN bkd_session_id TEXT;

COMMENT ON COLUMN stage_runs.bkd_session_id IS
  'BKD agent session token (Claude Code externalSessionId). Agent stage 才有；机械 checker 留 NULL';

-- 走 partial index：仅给非 NULL 行建索引，覆盖反向查询
-- (找某个 BKD session 对应的 stage_run) 而不为机械 stage 的大量 NULL 占空间。
CREATE INDEX IF NOT EXISTS idx_stage_runs_bkd_session
  ON stage_runs(bkd_session_id)
  WHERE bkd_session_id IS NOT NULL;
