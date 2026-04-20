-- Alert: stuck session
--
-- 触发：issue 处于 working 状态超过 stage 对应 SLA
-- 频率：每 5 分钟
-- 起步动作：Lark 通知
-- 升级动作：BKD cancel-issue + 开 GH incident
--
-- 覆盖缺陷：原设计无阶段超时熔断。如果 vm-node04 SSH 挂、agent 死循环、
--   BKD webhook 丢失，整条链路永远不 session.completed，n8n 侧没兜底。
--
-- SLA 取值拍脑袋：analyze 15 min / dev 30 min / ci 10 min / 其它 20 min。
--   跑一段时间后看 P95 数据再调。

SELECT
  s.issue_id,
  s.req_id,
  s.stage,
  s.title,
  s.bkd_updated_at,
  ROUND(EXTRACT(EPOCH FROM (now() - s.bkd_updated_at)) / 60)::INT AS stuck_min,
  CASE s.stage
    WHEN 'analyze' THEN 15
    WHEN 'dev'     THEN 30
    WHEN 'ci'      THEN 10
    ELSE              20
  END AS sla_min
FROM bkd_snapshot s
WHERE s.status = 'working'
  AND now() - s.bkd_updated_at > CASE s.stage
    WHEN 'analyze' THEN interval '15 minutes'
    WHEN 'dev'     THEN interval '30 minutes'
    WHEN 'ci'      THEN interval '10 minutes'
    ELSE              interval '20 minutes'
  END
  -- 跳过已登记的 incident，防重复 alert
  AND NOT EXISTS (
    SELECT 1 FROM event_log e
    WHERE e.kind = 'check.violation'
      AND e.issue_id = s.issue_id
      AND e.extras->>'rule' = 'stuck_session'
      AND e.ts > now() - interval '1 hour'
  )
ORDER BY stuck_min DESC;
