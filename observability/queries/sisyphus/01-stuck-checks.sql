-- Dashboard: stuck checks (sisyphus 内置 checker 钻牛角尖)
--
-- 数据源：sisyphus 主库（orchestrator）→ 表 artifact_checks（M1 PR #3 引入）
-- 用途：找出最近 24h 同一 (req_id, stage) 上 checker 连续 fail 超过 3 次的组合，
--   通常意味着 sisyphus 在一个修不动的地方循环重试，应人工介入。
-- 推荐告警阈值（人工配 Metabase Alert 参考）：
--   - 命中 1 行即可告警（任何 REQ 出现 fail_count ≥ 3 都值得看）
--   - 进阶：只要求 fail_count ≥ 5 再告，过滤正常重试抖动
--
-- 列含义：
--   req_id        卡住的需求 ID
--   stage         对应的 sisyphus 阶段（staging-test / pr-ci-watch / ...）
--   fail_count    24h 内 passed=false 次数
--   total_count   24h 内总 check 次数（算 fail ratio 用）
--   first_fail    最早一次 fail 的时间
--   last_fail     最近一次 fail 的时间
--   last_cmd      最近一次 fail 实际跑的命令（排查线索）
--   last_stderr   最近一次 fail 的 stderr_tail

SELECT
    req_id,
    stage,
    COUNT(*) FILTER (WHERE passed = false) AS fail_count,
    COUNT(*)                                AS total_count,
    MIN(checked_at) FILTER (WHERE passed = false) AS first_fail,
    MAX(checked_at) FILTER (WHERE passed = false) AS last_fail,
    (ARRAY_AGG(cmd         ORDER BY checked_at DESC)
        FILTER (WHERE passed = false))[1] AS last_cmd,
    (ARRAY_AGG(stderr_tail ORDER BY checked_at DESC)
        FILTER (WHERE passed = false))[1] AS last_stderr
FROM artifact_checks
WHERE checked_at > now() - interval '24 hours'
GROUP BY req_id, stage
HAVING COUNT(*) FILTER (WHERE passed = false) > 3
ORDER BY fail_count DESC, last_fail DESC;
