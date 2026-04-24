-- Q17: dedup retry 率监控（每小时 pending/done 分布）
-- pending_or_crashed: processed_at IS NULL（首次处理崩溃或正在处理中）
-- done: processed_at IS NOT NULL（已成功处理）
-- 健康环境 pending_or_crashed 应接近 0。

SELECT date_trunc('hour', seen_at)::timestamp AS hour,
       COUNT(*) FILTER (WHERE processed_at IS NULL)         AS pending_or_crashed,
       COUNT(*) FILTER (WHERE processed_at IS NOT NULL)     AS done
FROM event_seen
WHERE seen_at > now() - interval '24 hours'
GROUP BY 1 ORDER BY 1 DESC;
