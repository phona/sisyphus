-- REQ-fix-pr-queue-health-monitoring-1777789759: PR base drift 检测表
--
-- 每 30min cron 扫 OPEN PR，记录落后 main 的 commit 数 + 冲突预测。
-- 供 Metabase Q19-pr-drift / Q20-pr-aging 看板查询。
-- PRIMARY KEY 含 checked_at：允许同一 PR 多次记录（历史趋势）。

CREATE TABLE pr_drift_log (
    pr_number       INTEGER     NOT NULL,
    repo            TEXT        NOT NULL,  -- 'phona/sisyphus' / 'phona/ttpos-flutter' 等
    base_sha        TEXT        NOT NULL,  -- 检测时 target branch HEAD SHA
    behind_count    INTEGER     NOT NULL,  -- PR head 落后 base branch 的 commit 数
    conflict_predicted BOOLEAN  NOT NULL,  -- GitHub mergeable=false / mergeable_state=dirty
    drift_kind      TEXT,                  -- 'pure-lint-drift' / 'semantic-drift' / NULL(<= threshold)
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo, pr_number, checked_at)
);

CREATE INDEX idx_pr_drift_log_latest
    ON pr_drift_log (repo, pr_number, checked_at DESC);
