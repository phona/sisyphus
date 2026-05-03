-- Q21: prompt cache hit rate — 按 stage / model 聚合（最近 7 天）
-- REQ-feat-agent-turns-collector-1777796671
--
-- cache_hit_pct = token_cache_read / token_in。
-- 低命中率 = 高成本 = prompt 结构需优化（system prompt 没进 cache / 频繁变动）。
-- 仅统计 assistant turn（role='assistant'），token_in IS NOT NULL 的行。
-- 需要 agent_turns_collector_enabled=true 并跑过至少一轮后才有数据。
--
-- 建议 Visualization：Bar chart，X=stage，Y=cache_hit_pct，series=model

SELECT
    sr.stage,
    sr.model,
    COUNT(at.id)                                                         AS turn_count,
    SUM(at.token_in)                                                     AS total_token_in,
    SUM(at.token_cache_read)                                             AS total_cache_read,
    ROUND(
        100.0 * SUM(at.token_cache_read) / NULLIF(SUM(at.token_in), 0),
        2
    )                                                                    AS cache_hit_pct
FROM agent_turns at
JOIN stage_runs sr ON sr.bkd_issue_id = at.issue_id
WHERE at.started_at > NOW() - INTERVAL '7 days'
  AND at.role = 'assistant'
  AND at.token_in IS NOT NULL
GROUP BY sr.stage, sr.model
ORDER BY cache_hit_pct ASC NULLS LAST;
