-- stage_runs：记录每次 stage 调用的起止、产出、成本（M14e）。
-- 与 artifact_checks 互补：artifact_checks 关心 checker 通过/不通过，stage_runs 关心
-- agent 侧（谁跑的、模型、token、耗时），用于反哺质量改进 & 并行加速比 & 成本分析。
-- 埋点由调用方按需写入，不强制每个 action 都落，可逐步推广。

CREATE TABLE IF NOT EXISTS stage_runs (
    id              BIGSERIAL PRIMARY KEY,
    req_id          TEXT NOT NULL,
    stage           TEXT NOT NULL,
    parallel_id     TEXT,                 -- 并行派发下的分支 ID（fanout_dev 等）
    agent_type      TEXT,                 -- 哪类 agent（coder / verifier / fixer / ...）
    model           TEXT,                 -- 跑这次的模型名
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    outcome         TEXT,                 -- pass / fail / cancelled / escalated
    fail_reason     TEXT,
    token_in        BIGINT,
    token_out       BIGINT,
    duration_sec    REAL
);

CREATE INDEX IF NOT EXISTS idx_stage_runs_req ON stage_runs(req_id);
CREATE INDEX IF NOT EXISTS idx_stage_runs_stage_outcome
    ON stage_runs(stage, outcome, started_at DESC);
