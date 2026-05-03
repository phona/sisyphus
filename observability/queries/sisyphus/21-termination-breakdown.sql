-- Dashboard: REQ 终态分布 — 按 terminal_outcome 切分资源消耗（#244）
--
-- 数据源：req_state + stage_runs
-- 用途：回答"这周烧的 token / stage 次数里，多少是最终被 reject / 废弃的 REQ 浪费的？"
-- NULL bucket = 终态尚未回填（pipeline 仍在跑，或 best-effort 未覆盖的路径）
--
-- 列含义：
--   terminal_outcome  终态标签（merged / sisyphus-escalated / abandoned-by-user / NULL 等）
--   req_count         该 outcome 下的 REQ 数
--   total_stage_runs  关联的 stage_runs 条数（proxy for compute time / agent calls）
--   total_token_in    input token 之和（NULL stage_runs 行用 0 填充）
--   total_token_out   output token 之和
--   total_tokens      token_in + token_out
--   est_cost_usd      预估费用（$3/M input + $15/M output，Claude Opus）
--   avg_wall_clock_h  平均 wall-clock 耗时（小时，req 粒度）

SELECT
    COALESCE(r.terminal_outcome, 'NULL (unknown)')           AS terminal_outcome,
    COUNT(DISTINCT r.req_id)                                 AS req_count,
    COUNT(s.id)                                              AS total_stage_runs,
    COALESCE(SUM(s.token_in),  0)                           AS total_token_in,
    COALESCE(SUM(s.token_out), 0)                           AS total_token_out,
    COALESCE(SUM(s.token_in),  0)
        + COALESCE(SUM(s.token_out), 0)                     AS total_tokens,
    ROUND(
        (COALESCE(SUM(s.token_in),  0) *  3.0 / 1000000.0
       + COALESCE(SUM(s.token_out), 0) * 15.0 / 1000000.0)::numeric,
        4
    )                                                        AS est_cost_usd,
    ROUND(
        AVG(
            EXTRACT(EPOCH FROM (
                COALESCE(s_agg.last_ended, r.updated_at) - r.created_at
            )) / 3600.0
        )::numeric,
        2
    )                                                        AS avg_wall_clock_h
FROM req_state r
LEFT JOIN stage_runs s
    ON s.req_id = r.req_id
    AND s.started_at > now() - interval '30 days'
LEFT JOIN (
    SELECT req_id,
           MAX(ended_at) AS last_ended
      FROM stage_runs
     WHERE started_at > now() - interval '30 days'
     GROUP BY req_id
) s_agg ON s_agg.req_id = r.req_id
WHERE r.created_at > now() - interval '30 days'
GROUP BY r.terminal_outcome
ORDER BY total_tokens DESC;
