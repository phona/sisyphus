-- Q23: first assistant turn token composition — 按 stage 看 first-turn 重读占比
-- REQ-feat-agent-turns-collector-1777796671
--
-- 专为 #240 决策设计："fixer same-session continuation 是否值得"。
-- 每个 issue 的第一条 assistant turn（turn_idx 最小的 role='assistant' 行）代表
-- agent 开始工作前读取 context 的成本：cache_read 占比高 = 重读比例大 = 续 session
-- 可节省大量 token；占比低 = 多数是新 prompt，续 session 收益有限。
-- 按 stage 聚合，fixer 的 avg_cache_read_pct 最有参考价值。
--
-- 建议 Visualization：Bar chart，X=stage，Y=avg_cache_read_pct，辅助线 80%

WITH first_assistant AS (
    SELECT DISTINCT ON (at.issue_id)
        at.issue_id,
        at.token_in,
        at.token_cache_read,
        at.token_cache_create
    FROM agent_turns at
    WHERE at.role = 'assistant'
      AND at.token_in IS NOT NULL
    ORDER BY at.issue_id, at.turn_idx ASC
)
SELECT
    sr.stage,
    COUNT(fa.issue_id)                                                    AS run_count,
    ROUND(AVG(fa.token_in)::numeric, 0)                                   AS avg_token_in,
    ROUND(AVG(fa.token_cache_read)::numeric, 0)                           AS avg_cache_read,
    ROUND(AVG(fa.token_cache_create)::numeric, 0)                         AS avg_cache_create,
    ROUND(
        100.0 * AVG(fa.token_cache_read) / NULLIF(AVG(fa.token_in), 0),
        2
    )                                                                     AS avg_cache_read_pct
FROM first_assistant fa
JOIN stage_runs sr ON sr.bkd_issue_id = fa.issue_id
WHERE sr.started_at > NOW() - INTERVAL '7 days'
GROUP BY sr.stage
HAVING COUNT(fa.issue_id) >= 3
ORDER BY avg_cache_read_pct ASC NULLS LAST;
