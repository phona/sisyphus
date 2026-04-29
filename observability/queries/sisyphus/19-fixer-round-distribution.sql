-- Q19: Fixer round 分布与 cap 变化对比（REQ-fixer-cap-5-to-2-1777420814）
--
-- 数据源：sisyphus 主库 → 表 req_state
-- 用途：跟踪每个 REQ 的 fixer round 数，对比 cap=5 → cap=2 后的分布变化，
--   观察 2 轮 cap 是否导致 escalate 率上升（如果不够，应调回 3）。
--
-- 列含义：
--   fixer_round      该 REQ 经历的 fixer 轮数（从 ctx.fixer_round 读取）
--   n_reqs           该轮数的 REQ 数量
--   pct              占比（%）
--   n_escalated      其中最终 escalated 的数量
--   escalate_rate    escalate 率（%）

WITH fixer_reqs AS (
    SELECT
        req_id,
        COALESCE((context->>'fixer_round')::int, 0) AS fixer_round,
        state
    FROM req_state
    WHERE created_at > now() - interval '30 days'
      AND (context->>'fixer_round')::int > 0
)
SELECT
    fixer_round,
    COUNT(*)                                    AS n_reqs,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct,
    COUNT(*) FILTER (WHERE state = 'escalated') AS n_escalated,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE state = 'escalated')
              / NULLIF(COUNT(*), 0),
        2
    )                                           AS escalate_rate
FROM fixer_reqs
GROUP BY fixer_round
ORDER BY fixer_round;
