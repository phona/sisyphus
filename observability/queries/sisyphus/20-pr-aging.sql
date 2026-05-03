-- Q20: PR queue 年龄 + dirty rate 分桶统计
-- REQ-fix-pr-queue-health-monitoring-1777789759
--
-- 统计 pr_drift_log 里各 PR 的首次发现时间（首次 checked_at）作为 proxy 年龄，
-- 按 <1d / 1-3d / 3-7d / >7d 分桶，输出 dirty rate（drift_kind != NULL）和平均 behind_count。
--
-- NOTE: pr_drift_log 只记录 behind > threshold 的 PR；newly-opened fresh PR（threshold 内）
-- 不在此表，年龄统计不含 fresh PR。若需完整 PR 生命周期统计，需扩展 pr_links 表（TODO）。
-- 建议 Visualization：Bar chart，X = age_bucket，Y = pr_count，颜色按 dirty_rate 渐变

WITH first_seen AS (
    SELECT
        repo,
        pr_number,
        MIN(checked_at)                                    AS first_seen_at,
        MAX(behind_count)                                  AS max_behind,
        BOOL_OR(drift_kind IS NOT NULL)                    AS is_dirty
    FROM pr_drift_log
    GROUP BY repo, pr_number
),
bucketed AS (
    SELECT
        repo,
        pr_number,
        max_behind,
        is_dirty,
        NOW() - first_seen_at                              AS age,
        CASE
            WHEN NOW() - first_seen_at < INTERVAL '1 day'  THEN '<1d'
            WHEN NOW() - first_seen_at < INTERVAL '3 days' THEN '1-3d'
            WHEN NOW() - first_seen_at < INTERVAL '7 days' THEN '3-7d'
            ELSE '>7d'
        END                                                AS age_bucket
    FROM first_seen
)
SELECT
    repo,
    age_bucket,
    COUNT(*)                                               AS pr_count,
    ROUND(AVG(max_behind), 1)                              AS avg_behind_count,
    ROUND(100.0 * SUM(CASE WHEN is_dirty THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                           AS dirty_rate_pct
FROM bucketed
GROUP BY repo, age_bucket
ORDER BY repo, CASE age_bucket
    WHEN '<1d'  THEN 1
    WHEN '1-3d' THEN 2
    WHEN '3-7d' THEN 3
    ELSE 4
END;
