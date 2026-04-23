-- Dashboard: fanout_dev 并行加速比（M14e）
--
-- 数据源：sisyphus 主库 → 表 stage_runs
-- 用途：fanout_dev 派多个 coder 并发跑时，实际墙钟 vs 串行总耗时 的比值。
--   speedup = Σ(单分支 duration) / max(分支) = "如果串行要 X 秒，并行只用 Y 秒"
-- 推荐告警阈值：
--   - speedup 接近 1 → 并行没收益（可能是锁/瓶颈）
--   - speedup > N（分支数）× 0.8 → 接近理论上限，健康
--
-- 列含义：
--   req_id            需求 ID
--   branches          并行分支数（parallel_id 去重）
--   sum_duration_sec  各分支 duration 之和（= 串行等效）
--   max_duration_sec  最慢分支 duration（= 并行墙钟）
--   speedup_ratio     加速比
--   started_at        最早分支起点

SELECT
    req_id,
    COUNT(DISTINCT parallel_id)               AS branches,
    ROUND(SUM(duration_sec)::numeric, 2)      AS sum_duration_sec,
    ROUND(MAX(duration_sec)::numeric, 2)      AS max_duration_sec,
    ROUND(
        (SUM(duration_sec) / NULLIF(MAX(duration_sec), 0))::numeric, 2
    )                                         AS speedup_ratio,
    MIN(started_at)                           AS started_at
FROM stage_runs
WHERE stage = 'dev'
  AND parallel_id IS NOT NULL
  AND duration_sec IS NOT NULL
  AND started_at > now() - interval '30 days'
GROUP BY req_id
HAVING COUNT(DISTINCT parallel_id) >= 2
ORDER BY started_at DESC;
