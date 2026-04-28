-- REQ-staging-test-baseline-diff-1777343371: baseline diff for staging_test
--
-- staging_test checker 现在两阶段跑：
--   Phase 1: checkout main HEAD，跑同套 ci-unit-test + ci-integration-test，收集 "baseline_failures"
--   Phase 2: checkout feat/<REQ>，跑同套，收集 "pr_failures"
--   真 fail set = pr_failures - baseline_failures
--     空 → staging-test.pass（PR 没引入新失败；main 自己坏了不是 agent 的锅）
--     非空 → staging-test.fail（verifier 收到 baseline 差量上下文）
--
-- baseline_results 是缓存表：同 main HEAD SHA 24h 内复用，避免每个 REQ 都重跑 main 一遍。
-- cache_key = "baseline:staging_test:<main_head_sha>"
-- repo_results JSONB = {"repo-basename": true/false, ...}（true = passed）

CREATE TABLE IF NOT EXISTS baseline_results (
    id          BIGSERIAL PRIMARY KEY,
    cache_key   TEXT NOT NULL,
    main_sha    TEXT NOT NULL,
    repo_results JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 按 cache_key + created_at 查最新记录（TTL 窗口过滤）
CREATE UNIQUE INDEX IF NOT EXISTS idx_baseline_results_cache_key
    ON baseline_results(cache_key);
