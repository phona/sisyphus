-- Dashboard: active REQ overview (当前在飞 REQ + 卡在哪个 stage 多久)
--
-- 数据源：sisyphus 主库（orchestrator）
--   - req_state：REQ 生命周期状态（state / context / 最近 history ts）
--   - artifact_checks：最近一次 check 的 stage / passed / checked_at
--
-- 用途：一眼看出当前所有在飞 REQ 正卡在哪个 stage、最近 check 的结果。
--   运维早上开 Metabase 先看这张，发现超过阈值的优先处理。
-- 推荐告警阈值：
--   - stuck_min > 30 且 last_passed=false → 大概率卡死，应介入
--   - 同一 stage 有 ≥ 3 条 REQ 并发卡住 → 该 stage 可能整体劣化
--
-- 列含义：
--   req_id           REQ ID
--   project_id       项目 ID（来自 req_state）
--   state            当前状态机 state（analyzing / dev-running / ...）
--   last_stage       最近一次 check 的 stage
--   last_passed      最近一次 check 是否通过
--   last_cmd         最近一次 check 跑的命令
--   last_checked_at  最近一次 check 时间
--   stuck_min        REQ 从上次 state 变化到现在的分钟数
--   bugfix_rounds    已用 bugfix round 数（context 里）
--   recent_fail_24h  近 24h 该 REQ 在 last_stage 上 fail 次数

WITH last_check AS (
    SELECT DISTINCT ON (req_id)
        req_id,
        stage        AS last_stage,
        passed       AS last_passed,
        cmd          AS last_cmd,
        stderr_tail  AS last_stderr,
        checked_at   AS last_checked_at
    FROM artifact_checks
    ORDER BY req_id, checked_at DESC
),
recent_fail AS (
    SELECT
        req_id,
        stage,
        COUNT(*) AS fail_count_24h
    FROM artifact_checks
    WHERE checked_at > now() - interval '24 hours'
      AND passed = false
    GROUP BY req_id, stage
)
SELECT
    r.req_id,
    r.project_id,
    r.state,
    lc.last_stage,
    lc.last_passed,
    lc.last_cmd,
    lc.last_checked_at,
    ROUND(EXTRACT(EPOCH FROM (now() - r.updated_at)) / 60)::INT AS stuck_min,
    COALESCE((r.context->>'bugfix_round')::INT, 0) AS bugfix_rounds,
    COALESCE(rf.fail_count_24h, 0) AS recent_fail_24h
FROM req_state r
LEFT JOIN last_check lc USING (req_id)
LEFT JOIN recent_fail rf
    ON rf.req_id = r.req_id AND rf.stage = lc.last_stage
WHERE r.state NOT IN ('done', 'escalated')
ORDER BY stuck_min DESC;
