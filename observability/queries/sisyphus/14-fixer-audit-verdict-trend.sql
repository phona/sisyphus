-- Q14: Fixer audit verdict 趋势（按天）
-- 回答：fixer 改动的质量随时间如何变化？test-hack / code-lobotomy 有没有在增加？
-- 只看 after-fix 二次 verify 的审计记录（audit IS NOT NULL）。

SELECT
    date_trunc('day', made_at)::date AS day,
    audit->>'verdict'                AS verdict,
    COUNT(*)                         AS n
FROM verifier_decisions
WHERE audit IS NOT NULL
  AND made_at > now() - interval '30 days'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
