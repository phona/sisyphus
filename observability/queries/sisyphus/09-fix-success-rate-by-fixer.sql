-- Dashboard: fixer 修复成功率（M14e）
--
-- 数据源：sisyphus 主库 → 表 verifier_decisions
-- 用途：verifier 派了 fixer 修代码后，下一轮是不是真的 pass 了。
--   看哪类 fixer 最靠谱，哪类 scope（file / module / stage-wide）修得准。
-- 推荐告警阈值：
--   - 某 fixer 修复成功率 < 50% 且样本 ≥ 10 → 该 fixer 策略要换或加强 prompt
--   - scope=stage-wide 成功率远低于 file → 过度放大，先缩 scope
--
-- 列含义：
--   decision_fixer    被派去修的角色（coder / specialized-fixer / ...）
--   decision_scope    修复范围
--   total_fixes       30d 内派发且已有 outcome 的次数
--   successful_fixes  下一轮实测 pass 的次数
--   success_rate_pct  修复成功率百分比

SELECT
    decision_fixer,
    decision_scope,
    COUNT(*)                                                AS total_fixes,
    COUNT(*) FILTER (WHERE actual_outcome = 'pass')         AS successful_fixes,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE actual_outcome = 'pass')
              / NULLIF(COUNT(*), 0),
        2
    )                                                       AS success_rate_pct
FROM verifier_decisions
WHERE decision_action = 'fix'
  AND made_at > now() - interval '30 days'
  AND actual_outcome IS NOT NULL
GROUP BY decision_fixer, decision_scope
HAVING COUNT(*) >= 3
ORDER BY success_rate_pct ASC, total_fixes DESC;
