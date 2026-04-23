-- Dashboard: verifier 判决准确率（M14e）
--
-- 数据源：sisyphus 主库 → 表 verifier_decisions
-- 用途：看 verifier 每类决策（fix / escalate / retry / pass）判得对不对。
--   仅统计已被后续事实回填的 decision（actual_outcome 非空）。
-- 推荐告警阈值：
--   - 某 decision_action 的准确率 < 60% 且样本 ≥ 20 → verifier prompt 需调优
--   - escalate 误判率高 → 过度保守，浪费人工
--   - fix 误判率高 → 换 fixer 策略
--
-- 列含义：
--   decision_action    verifier 选了啥（fix / escalate / retry / pass）
--   total_decisions    30d 内该 action 的判决总数（已回填 outcome）
--   correct_decisions  后续验证证明判对的次数
--   accuracy_pct       准确率百分比
--   high_conf_n        confidence=high 的数量
--   high_conf_correct  高置信度中判对的
--   high_conf_acc_pct  高置信度准确率

SELECT
    decision_action,
    COUNT(*)                                                AS total_decisions,
    COUNT(*) FILTER (WHERE decision_correct = true)         AS correct_decisions,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE decision_correct = true)
              / NULLIF(COUNT(*), 0),
        2
    )                                                       AS accuracy_pct,
    COUNT(*) FILTER (WHERE decision_confidence = 'high')    AS high_conf_n,
    COUNT(*) FILTER (
        WHERE decision_confidence = 'high' AND decision_correct = true
    )                                                       AS high_conf_correct,
    ROUND(
        100.0 * COUNT(*) FILTER (
            WHERE decision_confidence = 'high' AND decision_correct = true
        ) / NULLIF(COUNT(*) FILTER (WHERE decision_confidence = 'high'), 0),
        2
    )                                                       AS high_conf_acc_pct
FROM verifier_decisions
WHERE made_at > now() - interval '30 days'
  AND decision_correct IS NOT NULL
GROUP BY decision_action
ORDER BY total_decisions DESC;
