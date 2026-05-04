-- Q24: REQ trace view — 单 REQ 全生命周期事件按时间轴聚合 (REQ-feat-req-trace-view-381-v2-1777866643)
--
-- 数据源：sisyphus 主库 (orchestrator)
--   - req_state.history (JSONB array, jsonb_array_elements 展开) — state 转移
--   - stage_runs                                                   — agent 调用起止
--   - verifier_decisions                                           — verifier 判决
--   - artifact_checks                                              — 机械 checker 通过/失败
--
-- event_log 在 sisyphus_obs 库不在本 SQL 里 (跨库 view 不做; 见 0002_observability_views.sql)。
-- CLI scripts/sisyphus-trace.py 负责跨库时再合; 本 Q 只覆盖主库 4 表。
--
-- 用途：debug "REQ 卡住不动" — 一眼看到该 REQ 的 trans / stage / verify / check 时间轴。
-- 30 min 翻 BKD + grep 源码 → 30 s 看一张表。
--
-- 列：
--   ts         事件时间戳
--   kind       事件类别 (trans / stage / verify / check)
--   detail     单行可读描述
--
-- Metabase 用法：
--   1. New Question → SQL 模式 → sisyphus 数据源 → 粘贴本 SQL
--   2. Variables: 加 `req_id` (Text, Required), 模板 `{{req_id}}`
--   3. Visualization: Table, 按 ts ASC (SQL 自带 ORDER BY 已保证)
--
-- 命令行用法：
--   scripts/sisyphus-trace.py REQ-XXXX

WITH trans AS (
    SELECT
        (h.val->>'ts')::timestamptz                                     AS ts,
        'trans'::text                                                    AS kind,
        format(
            '%s → %s (event=%s action=%s)',
            COALESCE(h.val->>'from', '?'),
            COALESCE(h.val->>'to',   '?'),
            COALESCE(h.val->>'event','?'),
            COALESCE(h.val->>'action','?')
        )                                                                AS detail
    FROM req_state r,
         LATERAL jsonb_array_elements(r.history) AS h(val)
    WHERE r.req_id = {{req_id}}
),
stages AS (
    -- 每行 stage_runs 拆成 start + end 两个事件 (end 仅当 ended_at 非空)
    SELECT
        sr.started_at                                                    AS ts,
        'stage'::text                                                    AS kind,
        format(
            '%s start (run_id=%s agent=%s model=%s)',
            sr.stage,
            sr.id,
            COALESCE(sr.agent_type, '?'),
            COALESCE(sr.model,      '?')
        )                                                                AS detail
    FROM stage_runs sr
    WHERE sr.req_id = {{req_id}}

    UNION ALL

    SELECT
        sr.ended_at                                                      AS ts,
        'stage'::text                                                    AS kind,
        format(
            '%s end %s (token_in=%s token_out=%s dur=%ss)',
            sr.stage,
            COALESCE(sr.outcome, '?'),
            COALESCE(sr.token_in::text,  '?'),
            COALESCE(sr.token_out::text, '?'),
            COALESCE(ROUND(sr.duration_sec::numeric, 1)::text, '?')
        )                                                                AS detail
    FROM stage_runs sr
    WHERE sr.req_id = {{req_id}}
      AND sr.ended_at IS NOT NULL
),
verifies AS (
    SELECT
        vd.made_at                                                       AS ts,
        'verify'::text                                                   AS kind,
        format(
            '%s/%s → %s (conf=%s fixer=%s reason=%s)',
            vd.stage,
            vd.trigger,
            COALESCE(vd.decision_action,     '?'),
            COALESCE(vd.decision_confidence, '?'),
            COALESCE(vd.decision_fixer,      '-'),
            COALESCE(left(vd.decision_reason, 80), '-')
        )                                                                AS detail
    FROM verifier_decisions vd
    WHERE vd.req_id = {{req_id}}
),
checks AS (
    SELECT
        ac.checked_at                                                    AS ts,
        'check'::text                                                    AS kind,
        format(
            '%s %s (exit=%s dur=%ss cmd=%s)',
            ac.stage,
            CASE WHEN ac.passed THEN 'passed' ELSE 'failed' END,
            COALESCE(ac.exit_code::text,                       '?'),
            COALESCE(ROUND(ac.duration_sec::numeric, 1)::text, '?'),
            COALESCE(left(ac.cmd, 80),                         '-')
        )                                                                AS detail
    FROM artifact_checks ac
    WHERE ac.req_id = {{req_id}}
)
SELECT ts, kind, detail FROM trans
UNION ALL
SELECT ts, kind, detail FROM stages
UNION ALL
SELECT ts, kind, detail FROM verifies
UNION ALL
SELECT ts, kind, detail FROM checks
ORDER BY ts ASC;
