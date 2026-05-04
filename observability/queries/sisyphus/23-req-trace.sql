-- Q23 REQ Trace — single REQ full lifecycle timeline (#381)
--
-- 数据源：req_state (history JSONB) + stage_runs + verifier_decisions + artifact_checks
-- 用途：调试卡住的 REQ / 故障复盘；从四张表聚合同一 req_id 的所有事件，按时间排序
--
-- Metabase 参数：{{req_id}}（Text Variable）；留空则返回空集
-- CLI 用法：python3 scripts/sisyphus-trace.py <req_id>
--
-- 列含义：
--   ts          事件时间戳（UTC）
--   event_type  事件来源：state_transition / stage_start / stage_end / verifier / checker
--   stage       所属 stage（state_transition 时为 NULL）
--   outcome     结果标签（pass / fail / escalate / fix / ...）
--   summary     一行可读摘要
--   detail      补充信息（fail_reason / decision_reason / checker cmd / stderr 等）

WITH state_transitions AS (
    SELECT
        (h->>'ts')::timestamptz                                          AS ts,
        'state_transition'::text                                         AS event_type,
        NULL::text                                                       AS stage,
        NULL::text                                                       AS outcome,
        (h->>'from_state') || ' → ' || (h->>'to_state')                AS summary,
        COALESCE(h->>'event', '')                                        AS detail
    FROM req_state r,
         jsonb_array_elements(r.history) AS h
    WHERE r.req_id = {{req_id}}
),
stage_events AS (
    SELECT
        started_at                                                       AS ts,
        'stage_start'::text                                              AS event_type,
        stage,
        NULL::text                                                       AS outcome,
        stage || ' started'
            || COALESCE(' [' || agent_type || ']', '')                  AS summary,
        COALESCE(bkd_issue_id, '')                                       AS detail
    FROM stage_runs
    WHERE req_id = {{req_id}}
    UNION ALL
    SELECT
        ended_at                                                         AS ts,
        'stage_end'::text                                                AS event_type,
        stage,
        outcome,
        stage || ' ended: ' || COALESCE(outcome, '?')                   AS summary,
        COALESCE(fail_reason, '')                                        AS detail
    FROM stage_runs
    WHERE req_id = {{req_id}}
      AND ended_at IS NOT NULL
),
verifier_events AS (
    SELECT
        made_at                                                          AS ts,
        'verifier'::text                                                 AS event_type,
        stage,
        decision_action                                                  AS outcome,
        'verifier/' || stage || ': '
            || COALESCE(decision_action, '?')
            || COALESCE(' (fixer=' || decision_fixer || ')', '')        AS summary,
        COALESCE(decision_reason, '')                                    AS detail
    FROM verifier_decisions
    WHERE req_id = {{req_id}}
),
checker_events AS (
    SELECT
        checked_at                                                       AS ts,
        'checker'::text                                                  AS event_type,
        stage,
        CASE WHEN passed THEN 'pass' ELSE 'fail' END                    AS outcome,
        stage || ' checker: '
            || CASE WHEN passed
               THEN 'PASS'
               ELSE 'FAIL (exit=' || COALESCE(exit_code::text, '?') || ')'
               END                                                      AS summary,
        COALESCE(
            LEFT(stderr_tail, 160),
            LEFT(stdout_tail, 160),
            cmd,
            ''
        )                                                               AS detail
    FROM artifact_checks
    WHERE req_id = {{req_id}}
)
SELECT ts, event_type, stage, outcome, summary, detail
FROM (
    SELECT * FROM state_transitions
    UNION ALL
    SELECT * FROM stage_events
    UNION ALL
    SELECT * FROM verifier_events
    UNION ALL
    SELECT * FROM checker_events
) combined
ORDER BY ts NULLS LAST;
