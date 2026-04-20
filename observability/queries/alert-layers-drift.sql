-- Alert: layers drift
--
-- 触发：analyze-done 的 issue 没有任何 layer:* tag
-- 频率：每 5 分钟
-- 起步动作：Lark 通知（被动报告）
-- 升级动作：BKD 加 block-fanout tag，Router 路由到 escalate
--
-- 覆盖缺陷：architecture.md 里 "layers 双真相源" flaw
--   frontmatter.layers 和 tag.layer:* 可能不一致，analyze agent 漏写 tag
--   时 n8n fanout 少一路 spec，下游静默跑错。
--   这条规则抓"完全没写 layer:* tag"的情况（80% 的 drift）。
--   完整 frontmatter vs tag 对比需要读 git，留给后续 observer.py。

SELECT
  s.req_id,
  s.issue_id,
  s.title,
  s.tags,
  s.bkd_updated_at
FROM bkd_snapshot s
WHERE s.stage = 'analyze'
  AND s.status = 'review'
  AND s.bkd_updated_at > now() - interval '30 minutes'
  AND NOT EXISTS (
    SELECT 1 FROM unnest(s.tags) t WHERE t LIKE 'layer:%'
  )
  -- 跳过已登记的 incident，防重复 alert
  AND NOT EXISTS (
    SELECT 1 FROM event_log e
    WHERE e.kind = 'check.violation'
      AND e.req_id = s.req_id
      AND e.extras->>'rule' = 'layers_drift'
      AND e.ts > now() - interval '1 hour'
  )
ORDER BY s.bkd_updated_at DESC;
