-- Q20: Fixer 场景 verifier 决策分布（按 cap 变化前后对比）
--
-- 数据源：sisyphus 主库 → 表 verifier_decisions + req_state
-- 用途：cap=5 → cap=2 后，观察 verifier 在 fixer 场景下的决策分布变化：
--   - pass 率是否下降（2 轮不够修 → verifier 更多 escalate）
--   - escalate 率是否显著上升
--   - fix 率变化（循环被截断 → fix 决策减少）
-- 推荐告警阈值：
--   - escalate 率上升 ≥ 10pp（百分点）→ cap=2 可能过紧，考虑调回 3
--   - pass 率下降 ≥ 15pp → fixer 质量在退化或 cap 过紧
--
-- 列含义：
--   decision_action    verifier 决策（pass / fix / escalate）
--   total              30d 内该决策次数
--   pct                占比（%）

SELECT
    decision_action,
    COUNT(*) AS total,
    ROUND(
        100.0 * COUNT(*)
              / NULLIF(SUM(COUNT(*)) OVER (), 0),
        2
    ) AS pct
FROM verifier_decisions
WHERE decision_action IN ('pass', 'fix', 'escalate')
  AND made_at > now() - interval '30 days'
GROUP BY decision_action
ORDER BY total DESC;
