-- 补强 observability：在 orchestrator 主库加几个便于排查改进的 view
-- 注意：bkd_snapshot / event_log 在 sisyphus_obs 库，跨库 view 不做；
-- 这里放 req_state 维度的派生指标。

-- ═══ view: req_latency ═════════════════════════════════════════════════
-- 每个 REQ 每个 stage 进出时间差（按 history 相邻两个 ts 算耗时）
-- 可推导：per-stage 平均时长、慢 stage 排行
CREATE OR REPLACE VIEW req_latency AS
WITH h AS (
    SELECT
        r.req_id,
        r.state AS final_state,
        (t.val->>'to')::text AS stage,
        (t.val->>'event')::text AS enter_event,
        (t.val->>'action')::text AS action,
        (t.val->>'ts')::timestamptz AS entered_at,
        LEAD((t.val->>'ts')::timestamptz)
            OVER (PARTITION BY r.req_id ORDER BY t.ord) AS left_at
    FROM req_state r,
         LATERAL jsonb_array_elements(r.history) WITH ORDINALITY AS t(val, ord)
)
SELECT
    req_id, final_state, stage, enter_event, action,
    entered_at,
    left_at,
    EXTRACT(EPOCH FROM (COALESCE(left_at, now()) - entered_at))::INT AS duration_sec
FROM h;

-- ═══ view: req_summary ═════════════════════════════════════════════════
-- 每个 REQ 总览：花多久、几个 stage、是否 done、bugfix round 用了几次
CREATE OR REPLACE VIEW req_summary AS
SELECT
    r.req_id,
    r.project_id,
    r.state AS final_state,
    r.created_at,
    r.updated_at,
    EXTRACT(EPOCH FROM (r.updated_at - r.created_at))::INT AS total_sec,
    jsonb_array_length(r.history) AS total_steps,
    COALESCE((r.context->>'bugfix_round')::INT, 0) AS bugfix_rounds,
    (r.context->>'circuit_broken')::BOOL AS cb_tripped,
    r.context->>'intent_title' AS intent_title
FROM req_state r;

-- ═══ view: stage_stats ═════════════════════════════════════════════════
-- per-stage 聚合：平均耗时、一把过率（state=done 且这是唯一一次进 stage）
CREATE OR REPLACE VIEW stage_stats AS
SELECT
    stage,
    COUNT(*) AS enter_count,
    COUNT(DISTINCT req_id) AS req_count,
    ROUND(AVG(duration_sec)::numeric, 1) AS avg_sec,
    MIN(duration_sec) AS min_sec,
    MAX(duration_sec) AS max_sec,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_sec) AS p50_sec,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_sec) AS p95_sec
FROM req_latency
WHERE left_at IS NOT NULL   -- 还在跑的不算
GROUP BY stage;

-- ═══ view: failure_mode ════════════════════════════════════════════════
-- 被 escalated 的 REQ 原因分布
CREATE OR REPLACE VIEW failure_mode AS
SELECT
    COALESCE(context->>'escalated_reason', 'unknown') AS reason,
    COUNT(*) AS count,
    ARRAY_AGG(req_id ORDER BY updated_at DESC) AS recent_reqs
FROM req_state
WHERE state = 'escalated'
GROUP BY context->>'escalated_reason'
ORDER BY count DESC;
