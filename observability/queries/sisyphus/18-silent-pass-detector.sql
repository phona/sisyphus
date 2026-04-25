-- Q18: 机械 checker silent-pass 检测
--
-- 数据源：sisyphus 主库（orchestrator）→ 表 artifact_checks
-- 用途：找最近 24h `passed=true` 但**疑似没真跑**的 checker 记录 —— 即"沉默通过"。
--   spec_lint / dev_cross_check / staging_test / pr_ci_watch 各自源代码里写了
--   "refusing to silent-pass" 的 guard（empty-source / ran=0 / no-gha-checks-ran），
--   设计上零信号即 fail。本 query 是**事后兜底**：万一 guard 失效或被绕过，
--   能从 metrics 层把"通过了但其实没干活"的样本捞出来人工 review。
--
-- 三类信号（CASE 优先级从严到松）：
--   guard-leak  stdout/stderr 命中 "refusing to silent-pass" 但 passed=true
--               → checker 内有逻辑 bug：guard 行打了但 exit code 仍是 0
--   no-gha-pass pr_ci_watch 在 stdout 留下 "no-gha-checks-ran" 但 passed=true
--               → _classify 返回 no-gha 走 fail 分支，passed=true 不可能；
--                 出现说明分类逻辑被改坏或 stdout 串被复用
--   too-fast    duration_sec < 0.2 × 同 stage 7d P50（passed=true 样本）
--               → 跑得比中位数快 5×，大概率是脚本短路（grep 不到 → for 循环没跑 →
--                 ran=0 guard 没正确生效）。和 Q2（慢异常）是对称的快异常
--
-- 推荐告警阈值：
--   - 命中 1 行 silent_pass_kind ∈ ('guard-leak', 'no-gha-pass') → 立即介入
--     这两类信号在 checker 源码里已经设计成不可达 path，出现就是有 bug
--   - silent_pass_kind = 'too-fast' 单日 ≥ 3 条同 stage → 重算 P50 基线 / 查脚本短路
--
-- 列含义：
--   req_id           疑似 silent-pass 的 REQ
--   stage            sisyphus 阶段（spec_lint / dev_cross_check / staging_test / pr_ci_watch）
--   silent_pass_kind 触发哪类信号（见上方 CASE）
--   duration_sec     当次 check 实际耗时
--   p50_sec          同 stage 7d passed=true 样本的中位数（基线）
--   ratio            duration_sec / p50_sec（ < 0.2 视为异常）
--   evidence         stdout_tail + stderr_tail 各取首段（300 字符），人工 triage 用
--   cmd              具体命令
--   checked_at       发生时间
--
-- 样本不足（同 stage 7d passed=true 样本 < 20）→ 跳过该 stage 的 too-fast 判断，
-- 但 guard-leak / no-gha-pass 仍照常输出（绝对信号，不依赖基线）。

WITH baseline AS (
    SELECT
        stage,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_sec) AS p50_sec
    FROM artifact_checks
    WHERE checked_at > now() - interval '7 days'
      AND duration_sec IS NOT NULL
      AND passed = true
    GROUP BY stage
    HAVING COUNT(*) >= 20
)
SELECT
    c.req_id,
    c.stage,
    CASE
        WHEN COALESCE(c.stdout_tail, '') || COALESCE(c.stderr_tail, '')
             ILIKE '%refusing to silent-pass%'                THEN 'guard-leak'
        WHEN COALESCE(c.stdout_tail, '') ILIKE '%no-gha-checks-ran%' THEN 'no-gha-pass'
        WHEN c.duration_sec < b.p50_sec * 0.2                 THEN 'too-fast'
    END                                                       AS silent_pass_kind,
    ROUND(c.duration_sec::numeric, 2)                         AS duration_sec,
    ROUND(b.p50_sec::numeric, 2)                              AS p50_sec,
    ROUND((c.duration_sec / NULLIF(b.p50_sec, 0))::numeric, 2) AS ratio,
    LEFT(
        COALESCE(c.stdout_tail, '') || ' | ' || COALESCE(c.stderr_tail, ''),
        300
    )                                                          AS evidence,
    c.cmd,
    c.checked_at
FROM artifact_checks c
LEFT JOIN baseline b USING (stage)
WHERE c.checked_at > now() - interval '24 hours'
  AND c.passed = true
  AND (
        COALESCE(c.stdout_tail, '') || COALESCE(c.stderr_tail, '')
            ILIKE '%refusing to silent-pass%'
     OR COALESCE(c.stdout_tail, '') ILIKE '%no-gha-checks-ran%'
     OR (b.p50_sec IS NOT NULL AND c.duration_sec < b.p50_sec * 0.2)
  )
ORDER BY
    CASE
        WHEN COALESCE(c.stdout_tail, '') || COALESCE(c.stderr_tail, '')
             ILIKE '%refusing to silent-pass%' THEN 0
        WHEN COALESCE(c.stdout_tail, '') ILIKE '%no-gha-checks-ran%' THEN 1
        ELSE 2
    END,
    c.checked_at DESC;
