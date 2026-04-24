-- Q16: Fixer 改动文件分类分布（按周）
-- 回答：fixer 每周改了多少 src / tests / spec / config 文件？
-- tests 改动占比高 + src 改动少 → 可能在 hack 测试（需配合 Q15 看 verdict）。

SELECT
    date_trunc('week', made_at)::date                              AS week,
    SUM((audit->'files_by_category'->>'src')::int)                 AS src_changes,
    SUM((audit->'files_by_category'->>'tests')::int)               AS test_changes,
    SUM((audit->'files_by_category'->>'spec')::int)                AS spec_changes,
    SUM((audit->'files_by_category'->>'config')::int)              AS config_changes
FROM verifier_decisions
WHERE audit IS NOT NULL
  AND made_at > now() - interval '30 days'
GROUP BY 1
ORDER BY 1 DESC;
