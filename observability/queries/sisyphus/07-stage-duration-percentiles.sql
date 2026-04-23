-- Dashboard: stage 耗时分位数（M14e）
--
-- 数据源：sisyphus 主库 → 表 stage_runs
-- 用途：看每个 stage 的 P50 / P90 / P95 耗时，识别瓶颈 stage 和慢异常。
-- 推荐告警阈值：
--   - P95 比 P50 大 5x+ → 分布长尾严重，查个别 outlier
--   - 同 stage 周环比 P95 上涨 ≥ 50% → 外部依赖或代码退化
--
-- 列含义：
--   stage           sisyphus 阶段
--   runs            30d 内该 stage 有 duration 的样本数
--   p50 / p90 / p95 秒
--   max_duration    最慢一次（秒）
--   avg_duration    均值（秒）

SELECT
    stage,
    COUNT(*)                                                          AS runs,
    ROUND(
        percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec)::numeric, 2
    )                                                                 AS p50,
    ROUND(
        percentile_cont(0.9) WITHIN GROUP (ORDER BY duration_sec)::numeric, 2
    )                                                                 AS p90,
    ROUND(
        percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_sec)::numeric, 2
    )                                                                 AS p95,
    ROUND(MAX(duration_sec)::numeric, 2)                              AS max_duration,
    ROUND(AVG(duration_sec)::numeric, 2)                              AS avg_duration
FROM stage_runs
WHERE started_at > now() - interval '30 days'
  AND duration_sec IS NOT NULL
GROUP BY stage
HAVING COUNT(*) >= 5
ORDER BY p95 DESC NULLS LAST;
