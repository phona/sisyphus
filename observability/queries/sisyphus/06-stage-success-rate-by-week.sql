-- Dashboard: stage_runs 周度通过率（M14e）
--
-- 数据源：sisyphus 主库 → 表 stage_runs
-- 用途：看每个 stage 每周通过率走向，突降/抬升一眼看出。
--   区别于 03-stage-success-rate.sql（基于 artifact_checks 的 checker 粒度）：
--   这里是 agent 调用粒度，反映 coder/fixer/verifier 的实际表现。
-- 推荐告警阈值：
--   - 单周 pass_rate < 50% 且样本 ≥ 10 → 标红
--   - 周环比 pass_rate 下降 ≥ 20pp → 立即排查
--
-- 列含义：
--   week_start      ISO 周起点（周一）
--   stage           sisyphus 阶段
--   total_runs      当周该 stage 跑了多少次
--   pass_runs       outcome=pass 的次数
--   fail_runs       outcome=fail 的次数
--   pass_rate_pct   通过率百分比
--   unique_reqs     涉及的 REQ 数

SELECT
    date_trunc('week', started_at)           AS week_start,
    stage,
    COUNT(*)                                 AS total_runs,
    COUNT(*) FILTER (WHERE outcome = 'pass') AS pass_runs,
    COUNT(*) FILTER (WHERE outcome = 'fail') AS fail_runs,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE outcome = 'pass')
              / NULLIF(COUNT(*) FILTER (WHERE outcome IN ('pass', 'fail')), 0),
        2
    )                                        AS pass_rate_pct,
    COUNT(DISTINCT req_id)                   AS unique_reqs
FROM stage_runs
WHERE started_at > now() - interval '90 days'
  AND outcome IS NOT NULL
GROUP BY week_start, stage
ORDER BY week_start DESC, stage;
