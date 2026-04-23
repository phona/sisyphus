-- Dashboard: bugfix loop 异常（M14e）
--
-- 数据源：sisyphus 主库 → 表 stage_runs
-- 用途：同一 (req_id, stage) 反复 fail N 次仍在跑 → 卡在 bugfix loop，大概率钻牛角尖。
--   区别于 artifact_checks.01-stuck-checks.sql：这里看 agent 粒度而非 check 粒度，
--   结合 fail_reason 能看出 agent 是不是在同一个坑反复栽。
-- 推荐告警阈值：
--   - fail_count ≥ 5 即异常；≥ 8 建议直接 escalate
--
-- 列含义：
--   req_id          需求 ID
--   stage           阶段
--   fail_count      7d 内 outcome=fail 次数
--   total_runs      7d 内总 runs
--   last_fail_at    最近一次 fail 时间
--   last_fail_reason 最近一次 fail 的原因
--   top_fail_reason 7d 内出现次数最多的 fail_reason

WITH reason_counts AS (
    SELECT
        req_id, stage, fail_reason,
        COUNT(*) AS n,
        ROW_NUMBER() OVER (
            PARTITION BY req_id, stage
            ORDER BY COUNT(*) DESC
        ) AS rn
    FROM stage_runs
    WHERE outcome = 'fail'
      AND started_at > now() - interval '7 days'
      AND fail_reason IS NOT NULL
    GROUP BY req_id, stage, fail_reason
)
SELECT
    s.req_id,
    s.stage,
    COUNT(*) FILTER (WHERE s.outcome = 'fail')       AS fail_count,
    COUNT(*)                                         AS total_runs,
    MAX(s.started_at) FILTER (WHERE s.outcome = 'fail') AS last_fail_at,
    (
        SELECT s2.fail_reason
        FROM stage_runs s2
        WHERE s2.req_id = s.req_id AND s2.stage = s.stage
          AND s2.outcome = 'fail'
        ORDER BY s2.started_at DESC
        LIMIT 1
    )                                                AS last_fail_reason,
    (
        SELECT rc.fail_reason
        FROM reason_counts rc
        WHERE rc.req_id = s.req_id AND rc.stage = s.stage AND rc.rn = 1
    )                                                AS top_fail_reason
FROM stage_runs s
WHERE s.started_at > now() - interval '7 days'
GROUP BY s.req_id, s.stage
HAVING COUNT(*) FILTER (WHERE s.outcome = 'fail') >= 3
ORDER BY fail_count DESC, last_fail_at DESC;
