-- Dashboard: fail kind distribution (按关键词分类失败频次)
--
-- 数据源：sisyphus 主库（orchestrator）→ 表 artifact_checks
-- 用途：把 passed=false 的记录按 stderr_tail 里的关键词分桶，快速看"最近主要栽在哪"。
--   分桶是启发式的，命中第一个匹配桶即归类；未命中进 other。
-- 推荐告警阈值：
--   - other 占比 > 40% 说明桶覆盖不够，该补新关键词
--   - schema 桶突然飙升通常意味着契约变更未同步 → 优先排查
--   - timeout 桶飙升通常是外部依赖 / 网络问题
--
-- 列含义：
--   fail_kind        分类桶：schema / test / lint / build / timeout / perm / other
--   fail_count       近 7d 该桶命中次数
--   pct              该桶占所有失败的百分比
--   affected_stages  涉及的 stage 列表（去重）
--   sample_stderr    一条样本 stderr_tail（限 200 字符，排查用）

WITH failed AS (
    SELECT
        req_id,
        stage,
        stderr_tail,
        CASE
            WHEN stderr_tail ILIKE '%schema%'
              OR stderr_tail ILIKE '%validation%'
              OR stderr_tail ILIKE '%openapi%'
              OR stderr_tail ILIKE '%contract%'      THEN 'schema'
            WHEN stderr_tail ILIKE '%test%fail%'
              OR stderr_tail ILIKE '%assertion%'
              OR stderr_tail ILIKE '%pytest%'
              OR stderr_tail ILIKE '%expect%'        THEN 'test'
            WHEN stderr_tail ILIKE '%lint%'
              OR stderr_tail ILIKE '%ruff%'
              OR stderr_tail ILIKE '%eslint%'
              OR stderr_tail ILIKE '%mypy%'
              OR stderr_tail ILIKE '%type%error%'    THEN 'lint'
            WHEN stderr_tail ILIKE '%timeout%'
              OR stderr_tail ILIKE '%timed out%'
              OR stderr_tail ILIKE '%deadline%'      THEN 'timeout'
            WHEN stderr_tail ILIKE '%compile%error%'
              OR stderr_tail ILIKE '%build fail%'
              OR stderr_tail ILIKE '%cannot find%module%'
              OR stderr_tail ILIKE '%import%error%'  THEN 'build'
            WHEN stderr_tail ILIKE '%permission denied%'
              OR stderr_tail ILIKE '%unauthorized%'
              OR stderr_tail ILIKE '%forbidden%'     THEN 'perm'
            ELSE                                          'other'
        END AS fail_kind
    FROM artifact_checks
    WHERE checked_at > now() - interval '7 days'
      AND passed = false
)
SELECT
    fail_kind,
    COUNT(*)                               AS fail_count,
    ROUND(
        100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0),
        2
    )                                      AS pct,
    ARRAY_AGG(DISTINCT stage)              AS affected_stages,
    LEFT(MAX(stderr_tail), 200)            AS sample_stderr
FROM failed
GROUP BY fail_kind
ORDER BY fail_count DESC;
