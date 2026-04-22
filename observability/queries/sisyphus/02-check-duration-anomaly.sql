-- Dashboard: check duration anomaly (慢异常)
--
-- 数据源：sisyphus 主库（orchestrator）→ 表 artifact_checks
-- 用途：找 duration_sec > 同 stage 7d P95 × 2 的 check 记录。
--   这类异常常见原因：
--     - staging 起不来一直等 timeout
--     - 外部依赖（pypi / GH clone）偶发慢
--     - agent 的脚本卡在 prompt 里
-- 推荐告警阈值：
--   - 每日 1 条内可接受（偶发抖动）
--   - 出现 ≥ 3 条同 stage 慢异常，通常说明 P95 本身该重算或依赖普遍劣化
--
-- 列含义：
--   req_id, stage, duration_sec  当次 check 的慢记录
--   p95_sec                      同 stage 7d P95（对比基准）
--   ratio                        duration_sec / p95_sec
--   cmd                          具体命令
--   checked_at                   发生时间

WITH p95 AS (
    SELECT
        stage,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_sec) AS p95_sec
    FROM artifact_checks
    WHERE checked_at > now() - interval '7 days'
      AND duration_sec IS NOT NULL
    GROUP BY stage
    HAVING COUNT(*) >= 20  -- 样本不够 P95 不稳，直接跳过
)
SELECT
    c.req_id,
    c.stage,
    ROUND(c.duration_sec::numeric, 1)    AS duration_sec,
    ROUND(p95.p95_sec::numeric, 1)       AS p95_sec,
    ROUND((c.duration_sec / NULLIF(p95.p95_sec, 0))::numeric, 2) AS ratio,
    c.cmd,
    c.checked_at
FROM artifact_checks c
JOIN p95 USING (stage)
WHERE c.checked_at > now() - interval '24 hours'
  AND c.duration_sec > p95.p95_sec * 2
ORDER BY ratio DESC, c.checked_at DESC;
