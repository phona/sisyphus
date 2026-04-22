-- Dashboard: stage success rate (7d 各 stage checker 通过率)
--
-- 数据源：sisyphus 主库（orchestrator）→ 表 artifact_checks
-- 用途：看每个 sisyphus 阶段的 checker 7 天通过率，找出质量最差的 stage 优先改进。
--   "通过率" = passed=true 次数 / 总 check 次数。
--   注意：同一个 (req_id, stage) 会被多次 check（admission 通过前反复跑），
--         这里算的是 check 粒度通过率，不是 REQ 粒度一把过率。
-- 推荐告警阈值：
--   - 通过率 < 50% 的 stage 视为亚健康，需要看是 checker 太严还是 agent 太弱
--   - 通过率突降（本周 vs 上周 Δ > 20pp）应人工介入
--
-- 列含义：
--   stage           sisyphus 阶段
--   total_checks    7d 内该 stage 总 check 次数
--   passed_checks   其中 passed=true 的次数
--   failed_checks   其中 passed=false 的次数
--   pass_rate_pct   通过率百分比（两位小数）
--   unique_reqs     涉及到的 REQ 数
--   avg_duration    平均耗时（秒）

SELECT
    stage,
    COUNT(*)                                 AS total_checks,
    COUNT(*) FILTER (WHERE passed = true)    AS passed_checks,
    COUNT(*) FILTER (WHERE passed = false)   AS failed_checks,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE passed = true)
              / NULLIF(COUNT(*), 0),
        2
    )                                        AS pass_rate_pct,
    COUNT(DISTINCT req_id)                   AS unique_reqs,
    ROUND(AVG(duration_sec)::numeric, 2)     AS avg_duration
FROM artifact_checks
WHERE checked_at > now() - interval '7 days'
GROUP BY stage
ORDER BY pass_rate_pct ASC, total_checks DESC;
