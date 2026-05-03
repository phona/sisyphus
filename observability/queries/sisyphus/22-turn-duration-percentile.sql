-- Q22: turn duration percentiles — 每 stage P50 / P95 / P99（最近 7 天）
-- REQ-feat-agent-turns-collector-1777796671
--
-- 识别"哪个 stage 里哪一轮卡几分钟"。duration_ms 为 BKD 测量的单 turn 耗时。
-- 仅统计 assistant turn（role='assistant'），duration_ms IS NOT NULL。
-- P95 >> P50 说明分布有长尾，值得查个别 outlier；P99 供 SLA 基准参考。
--
-- 建议 Visualization：Table，按 p95_ms DESC 排序

SELECT
    sr.stage,
    COUNT(at.id)                                                          AS turn_count,
    ROUND(
        percentile_cont(0.50) WITHIN GROUP (ORDER BY at.duration_ms)::numeric, 0
    )                                                                     AS p50_ms,
    ROUND(
        percentile_cont(0.95) WITHIN GROUP (ORDER BY at.duration_ms)::numeric, 0
    )                                                                     AS p95_ms,
    ROUND(
        percentile_cont(0.99) WITHIN GROUP (ORDER BY at.duration_ms)::numeric, 0
    )                                                                     AS p99_ms,
    ROUND(MAX(at.duration_ms)::numeric, 0)                                AS max_ms
FROM agent_turns at
JOIN stage_runs sr ON sr.bkd_issue_id = at.issue_id
WHERE at.started_at > NOW() - INTERVAL '7 days'
  AND at.role = 'assistant'
  AND at.duration_ms IS NOT NULL
GROUP BY sr.stage
HAVING COUNT(at.id) >= 3
ORDER BY p95_ms DESC NULLS LAST;
