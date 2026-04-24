-- Q20: 过去 30d escalate reason 分布（critical alert）
-- 帮助识别哪类 reason 最频繁、哪块 prompt 最需要改进
SELECT
    date_trunc('day', created_at) AS day,
    reason,
    COUNT(*) AS n
FROM alerts
WHERE severity = 'critical'
  AND created_at > now() - INTERVAL '30 days'
GROUP BY 1, 2
ORDER BY 1 DESC, 3 DESC;
