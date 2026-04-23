-- Dashboard: REQ 维度 token 成本（M14e）
--
-- 数据源：sisyphus 主库 → 表 stage_runs
-- 用途：每个 REQ 走完花了多少 token，定位贵单、识别 bugfix-loop 烧 token 的元凶。
--   假设 input $3/M、output $15/M（Claude Opus），上层可按需替换价目。
-- 推荐告警阈值：
--   - 单 REQ total_tokens 超 2M → 大概率卡在 bugfix loop
--   - input/output 比 > 20:1 → prompt 膨胀或上下文设计有问题
--
-- 列含义：
--   req_id           需求 ID
--   total_runs       该 REQ 的 stage_runs 总数
--   total_token_in   所有 stage 的 input token 之和
--   total_token_out  所有 stage 的 output token 之和
--   total_tokens     token_in + token_out
--   est_cost_usd     预估费用（按 $3/M input + $15/M output 估）
--   first_started    首次 stage 开跑时间
--   last_ended       最后一次 stage 结束时间

SELECT
    req_id,
    COUNT(*)                                  AS total_runs,
    COALESCE(SUM(token_in), 0)                AS total_token_in,
    COALESCE(SUM(token_out), 0)               AS total_token_out,
    COALESCE(SUM(token_in), 0) + COALESCE(SUM(token_out), 0) AS total_tokens,
    ROUND(
        (COALESCE(SUM(token_in), 0)  * 3.0   / 1000000.0
       + COALESCE(SUM(token_out), 0) * 15.0  / 1000000.0)::numeric,
        4
    )                                         AS est_cost_usd,
    MIN(started_at)                           AS first_started,
    MAX(ended_at)                             AS last_ended
FROM stage_runs
WHERE started_at > now() - interval '30 days'
GROUP BY req_id
HAVING COALESCE(SUM(token_in), 0) + COALESCE(SUM(token_out), 0) > 0
ORDER BY total_tokens DESC
LIMIT 200;
