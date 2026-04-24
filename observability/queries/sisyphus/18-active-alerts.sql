-- Q18: 未处理告警（active alerts）
-- 实时看板：显示所有未 ack 的 alert，按时间倒序
SELECT
    created_at,
    severity,
    req_id,
    stage,
    reason,
    hint,
    suggested_action
FROM alerts
WHERE acknowledged_at IS NULL
ORDER BY created_at DESC
LIMIT 50;
