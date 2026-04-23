-- Dashboard: watchdog escalate 频率（M14e）
--
-- 数据源：sisyphus 主库 → 表 stage_runs + verifier_decisions
-- 用途：watchdog / verifier 触发 escalate（转人工或升级处理）的频率 + 主要 stage，
--   看"无人值守"目标达成度 — escalate 越少越好。
-- 推荐告警阈值：
--   - 日 escalate 数 ≥ 5 或日环比 ×2 → 系统整体在退化
--   - 某 stage 连续 3 天出现 escalate → 该 stage 策略要重新设计
--
-- 列含义：
--   day                  日期
--   stage                阶段
--   escalate_from_runs   stage_runs 中 outcome=escalated 的数量
--   escalate_from_verifier verifier_decisions 中 decision_action=escalate 的数量
--   total_escalates      合计

WITH run_esc AS (
    SELECT
        date_trunc('day', started_at)::date AS day,
        stage,
        COUNT(*) AS n
    FROM stage_runs
    WHERE outcome = 'escalated'
      AND started_at > now() - interval '30 days'
    GROUP BY 1, 2
),
vd_esc AS (
    SELECT
        date_trunc('day', made_at)::date AS day,
        stage,
        COUNT(*) AS n
    FROM verifier_decisions
    WHERE decision_action = 'escalate'
      AND made_at > now() - interval '30 days'
    GROUP BY 1, 2
)
SELECT
    COALESCE(r.day, v.day)                               AS day,
    COALESCE(r.stage, v.stage)                           AS stage,
    COALESCE(r.n, 0)                                     AS escalate_from_runs,
    COALESCE(v.n, 0)                                     AS escalate_from_verifier,
    COALESCE(r.n, 0) + COALESCE(v.n, 0)                  AS total_escalates
FROM run_esc r
FULL OUTER JOIN vd_esc v
    ON r.day = v.day AND r.stage = v.stage
ORDER BY day DESC, total_escalates DESC;
