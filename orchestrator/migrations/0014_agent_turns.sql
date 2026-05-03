-- REQ-feat-agent-turns-collector-1777796671: BKD turn-level data collector
--
-- 两件事：
-- 1. stage_runs 加 bkd_issue_id 列，让 agent_turns_collector 知道去哪拉 logs
--    （bkd_session_id 是 Claude Code externalSessionId，不是 BKD issue ID；
--     collector 调 /api/projects/{pid}/issues/{id}/logs 需要 issue ID）
-- 2. 新建 agent_turns 表：每行一条 BKD log entry（role × token × duration × tool_calls），
--    供 Q21-Q23 看 cache hit rate / 耗时分位 / first-turn 重读占比

ALTER TABLE stage_runs ADD COLUMN IF NOT EXISTS bkd_issue_id TEXT;

COMMENT ON COLUMN stage_runs.bkd_issue_id IS
  'BKD issue ID（非 externalSessionId）。agent_turns_collector 用它拉 /logs 接口。Agent stage 才有；机械 checker 留 NULL';

CREATE INDEX IF NOT EXISTS idx_stage_runs_bkd_issue
    ON stage_runs(bkd_issue_id)
    WHERE bkd_issue_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_turns (
    id                  BIGSERIAL       PRIMARY KEY,
    req_id              TEXT,
    issue_id            TEXT            NOT NULL,
    turn_idx            INT             NOT NULL,
    role                TEXT            NOT NULL,       -- user / assistant / tool_result
    tool_calls          JSONB,                          -- [{name, input_summary, duration_ms, error}]
    token_in            INT,
    token_out           INT,
    token_cache_read    INT,
    token_cache_create  INT,
    duration_ms         INT,
    started_at          TIMESTAMPTZ     NOT NULL,
    UNIQUE (issue_id, turn_idx)
);

CREATE INDEX IF NOT EXISTS idx_agent_turns_req
    ON agent_turns (req_id, started_at);
