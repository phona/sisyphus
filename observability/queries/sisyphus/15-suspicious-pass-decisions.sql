-- Q15: 可疑的 pass 决策（audit verdict != legitimate 但 action = pass）
-- 回答：有哪些 REQ 的 fixer 改动被判 pass，但 diff 审计显示有可疑行为（test-hack 等）？
-- 用于人工复盘"通过但可疑"的 case。

SELECT
    req_id,
    stage,
    audit->>'verdict'    AS verdict,
    audit->'red_flags'   AS flags,
    made_at
FROM verifier_decisions
WHERE decision_action = 'pass'
  AND audit->>'verdict' IS DISTINCT FROM 'legitimate'
  AND audit->>'verdict' IS NOT NULL
ORDER BY made_at DESC
LIMIT 50;
