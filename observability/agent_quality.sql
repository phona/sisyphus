-- Agent 质量监控 views（跑在 sisyphus_obs 库）。
-- 基础数据 = bkd_snapshot（5 min 镜像整个 BKD project issue 状态）。
--
-- 应用：kubectl -n sisyphus exec sisyphus-postgresql-0 -- \
--   psql -U sisyphus -d sisyphus_obs -f /tmp/agent_quality.sql
-- （orchestrator 启动时不自动 apply，独立维护，便于改 view 不用 release）

-- ═══ agent_quality: 每个 agent 角色的总览 ══════════════════════════════
-- first_pass_pct = 该 stage 在 REQ 里只出现一次（round=0）的比例
-- avg_duration_sec = BKD issue createdAt → bkd_updated_at 的平均秒数
CREATE OR REPLACE VIEW agent_quality AS
SELECT
    stage AS agent_role,
    COUNT(*) AS total_invocations,
    COUNT(DISTINCT req_id) AS distinct_reqs,
    ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT req_id), 0), 2)
        AS avg_invocations_per_req,
    COUNT(*) FILTER (WHERE status = 'done') AS done_count,
    COUNT(*) FILTER (WHERE status = 'review') AS review_count,
    COUNT(*) FILTER (WHERE 'ci:pass' = ANY(tags) OR 'result:pass' = ANY(tags))
        AS result_pass,
    COUNT(*) FILTER (WHERE 'ci:fail' = ANY(tags) OR 'result:fail' = ANY(tags))
        AS result_fail,
    ROUND(100.0 *
        COUNT(DISTINCT req_id) FILTER (WHERE round = 0 OR round IS NULL)
        / NULLIF(COUNT(DISTINCT req_id), 0), 1) AS first_pass_pct,
    AVG(EXTRACT(EPOCH FROM (bkd_updated_at - created_at)))::int
        AS avg_duration_sec
FROM bkd_snapshot
WHERE stage IS NOT NULL AND req_id IS NOT NULL
GROUP BY stage
ORDER BY total_invocations DESC;

-- ═══ bugfix_diagnosis: bugfix-agent 的诊断分布 ═════════════════════════
-- 看 dev-fix-agent 把 bug 归到哪类（code / test / spec / env）
-- 频繁 env-bug → sisyphus runner 环境问题，运维要改
-- 频繁 spec-bug → analyze/spec 阶段可能交付质量不够
CREATE OR REPLACE VIEW bugfix_diagnosis AS
SELECT
    CASE
        WHEN 'diagnosis:code-bug' = ANY(tags) THEN 'code-bug'
        WHEN 'diagnosis:test-bug' = ANY(tags) THEN 'test-bug'
        WHEN 'diagnosis:spec-bug' = ANY(tags) THEN 'spec-bug'
        WHEN 'diagnosis:env-bug' = ANY(tags) THEN 'env-bug'
        ELSE 'no-diagnosis'
    END AS diagnosis,
    COUNT(*) AS count,
    ARRAY_AGG(DISTINCT req_id ORDER BY req_id DESC) AS recent_reqs
FROM bkd_snapshot
WHERE stage = 'bugfix'
GROUP BY 1
ORDER BY count DESC;

-- ═══ session_failures: sessionStatus != completed 的 issue 统计 ════════
-- BKD agent session 挂了（崩 / timeout / max-turn）
-- 注意：bkd_snapshot 没存 session_status 字段（只有 status=todo/working/review/done）
-- 所以这个 view 靠 status != done 且 tags 没 pass/fail 来推断
CREATE OR REPLACE VIEW suspicious_sessions AS
SELECT
    req_id, stage, status, round, title, bkd_updated_at
FROM bkd_snapshot
WHERE stage IS NOT NULL
  AND status = 'review'  -- agent 说 done 但没完整 result tag
  AND NOT ('ci:pass' = ANY(tags) OR 'ci:fail' = ANY(tags)
           OR 'result:pass' = ANY(tags) OR 'result:fail' = ANY(tags)
           OR stage IN ('analyze', 'contract-spec', 'acceptance-spec', 'dev',
                        'done-archive', 'github-incident'))
ORDER BY bkd_updated_at DESC
LIMIT 50;
