-- Alert: bugfix runaway
--
-- 触发：自上次 integration CI pass 起算，同 REQ 累计 bugfix issue >= 3
-- 频率：每 10 分钟
-- 起步动作：Lark 通知
-- 升级动作：给 feat/REQ-xx 分支加 circuit-breaker tag，Router 自动 escalate
--
-- 覆盖缺陷：architecture.md 里 "熔断粒度太粗 / v3.1 熔断消失" flaws
--   v3.1 Router 的 CB_THRESHOLD 常量声明了但没引用，代码层无熔断。
--   这条规则按"上次 pass 到现在"分组计数，粒度比原来的
--   "全局 bugfix tag 数" 正确。
--
-- 后续改进：把 req_id 换成 (req_id, scenario)，颗粒度更准。

WITH last_pass AS (
  -- 每 REQ 最近一次 integration CI pass 的时间
  SELECT
    req_id,
    MAX(ts) AS pass_ts
  FROM event_log
  WHERE kind = 'webhook.received'
    AND target = 'integration'
    AND 'ci:pass' = ANY(tags)
  GROUP BY req_id
),
recent_bugfix AS (
  SELECT
    e.req_id,
    COUNT(DISTINCT e.issue_id) AS bugfix_count,
    MIN(e.ts) AS first_bugfix_ts,
    MAX(e.ts) AS last_bugfix_ts
  FROM event_log e
  LEFT JOIN last_pass lp USING (req_id)
  WHERE e.stage = 'bugfix'
    AND e.kind IN ('webhook.received', 'action.executed')
    AND e.ts > COALESCE(lp.pass_ts, '1970-01-01'::timestamptz)
  GROUP BY e.req_id
)
SELECT
  req_id,
  bugfix_count,
  first_bugfix_ts,
  last_bugfix_ts,
  EXTRACT(EPOCH FROM (last_bugfix_ts - first_bugfix_ts))::INT / 60 AS span_min
FROM recent_bugfix
WHERE bugfix_count >= 3
ORDER BY bugfix_count DESC;
