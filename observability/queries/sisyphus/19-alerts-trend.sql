-- Q19: 过去 24h 告警趋势（按小时 + severity）
SELECT
    date_trunc('hour', created_at) AS hour,
    severity,
    COUNT(*) AS n
FROM alerts
WHERE created_at > now() - INTERVAL '24 hours'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
