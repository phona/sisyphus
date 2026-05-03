-- Dashboard: 浪费 token Top-20（terminated 但未 merge 的 REQ）(#244)
--
-- 数据源：req_state + stage_runs
-- 用途：找出烧了最多 token 但最终 reject / 废弃的 REQ，识别 intent 类型规律
--   （删核心模块 / 大 rename / 新加 stage 等高 reject 率模式）
-- 覆盖 outcome：sisyphus-escalated / abandoned-by-user / merged-then-reverted
--   / pr-rejected / pr-closed-no-merge / replaced-by-other-req
--
-- 列含义：
--   req_id           需求 ID
--   terminal_outcome 终态标签
--   intent_summary   intent issue 标题（来自 ctx.intent_title，truncate 到 80 字符）
--   total_runs       stage_runs 总数
--   total_tokens     token_in + token_out 之和
--   est_cost_usd     预估费用（$3/M input + $15/M output，Claude Opus）
--   wall_clock_h     从 created_at 到最后一次 stage 结束的耗时（小时）
--   created_at       REQ 创建时间

SELECT
    r.req_id,
    r.terminal_outcome,
    LEFT(r.context->>'intent_title', 80)                     AS intent_summary,
    COUNT(s.id)                                              AS total_runs,
    COALESCE(SUM(s.token_in),  0)
        + COALESCE(SUM(s.token_out), 0)                     AS total_tokens,
    ROUND(
        (COALESCE(SUM(s.token_in),  0) *  3.0 / 1000000.0
       + COALESCE(SUM(s.token_out), 0) * 15.0 / 1000000.0)::numeric,
        4
    )                                                        AS est_cost_usd,
    ROUND(
        EXTRACT(EPOCH FROM (
            COALESCE(MAX(s.ended_at), r.updated_at) - r.created_at
        )) / 3600.0::numeric,
        2
    )                                                        AS wall_clock_h,
    r.created_at
FROM req_state r
LEFT JOIN stage_runs s ON s.req_id = r.req_id
WHERE r.terminal_outcome IN (
    'sisyphus-escalated',
    'abandoned-by-user',
    'merged-then-reverted',
    'pr-rejected',
    'pr-closed-no-merge',
    'replaced-by-other-req'
)
AND r.created_at > now() - interval '90 days'
GROUP BY r.req_id, r.terminal_outcome, r.context, r.created_at, r.updated_at
ORDER BY total_tokens DESC
LIMIT 20;
