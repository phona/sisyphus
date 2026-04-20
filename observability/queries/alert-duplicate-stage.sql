-- Alert: duplicate stage
--
-- 触发：同一 REQ 同一 stage 出现 >1 个活跃 issue
-- 频率：每 5 分钟
-- 起步动作：Lark 通知
-- 升级动作：保留最早创建的那个，对后面的 cancel-issue
--
-- 覆盖缺陷：architecture.md 里 "幂等性软闸 / Action 无幂等 key" flaws
--   n8n pod 重启或 HTTP 重试会导致 fanout_specs 重复创 issue。
--   Gate 只比较 >= expectedCount 会让重复 issue 在 gate pass 后继续跑，
--   两个 agent 同时写同一文件 / push 同一分支。
--
-- 注意：bugfix / ci / test-fix / reviewer 阶段允许多个（round-N 叠加），
--   不在此规则里。

SELECT
  req_id,
  stage,
  COUNT(*) AS active_count,
  array_agg(issue_id ORDER BY created_at) AS issue_ids,
  MIN(created_at) AS earliest,
  MAX(bkd_updated_at) AS latest_update
FROM bkd_snapshot
WHERE status IN ('working', 'review')
  AND stage IN (
    'analyze', 'dev-spec', 'contract-spec', 'accept-spec',
    'ui-spec', 'migration-spec', 'dev', 'accept', 'done-archive'
  )
GROUP BY req_id, stage
HAVING COUNT(*) > 1;
