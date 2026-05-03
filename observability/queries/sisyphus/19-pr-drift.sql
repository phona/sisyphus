-- Q19: PR base drift 最新快照
-- REQ-fix-pr-queue-health-monitoring-1777789759
--
-- 展示每个 OPEN PR 最近一次扫描时的 drift 状态：落后 commit 数 + 是否预测冲突。
-- 只看 behind_count > 5 的（低于阈值的 PR 不写 pr_drift_log）。
-- 按 drift_kind / behind_count DESC 排序：语义冲突优先，纯 lint drift 其次。
--
-- 建议 Visualization：Table，条件格式 conflict_predicted=true 标红

WITH latest AS (
    SELECT DISTINCT ON (repo, pr_number)
        repo,
        pr_number,
        base_sha,
        behind_count,
        conflict_predicted,
        drift_kind,
        checked_at
    FROM pr_drift_log
    ORDER BY repo, pr_number, checked_at DESC
)
SELECT
    repo,
    pr_number,
    behind_count,
    drift_kind,
    conflict_predicted,
    checked_at
FROM latest
WHERE behind_count > 5
ORDER BY
    conflict_predicted DESC,
    drift_kind,
    behind_count DESC;
