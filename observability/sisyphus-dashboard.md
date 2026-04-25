# Sisyphus checker 可观测性看板（M7 + M14e）

围绕 `artifact_checks`（M7）+ `stage_runs` / `verifier_decisions`（M14e）的 Metabase 看板设计。

**目的**：
- M7：让运维"一眼看出 sisyphus 内置 checker 在哪些 REQ / stage 上钻牛角尖"。
- M14e：反哺研发质量指标 —— stage 通过率、verifier 判决准确率、修复成功率、token 成本、并行加速比、watchdog escalate 频率。

**不接通知渠道**（飞书 / 邮件）。此版本只输出 SQL + Question 配置，人工看板兜底。通知通道另起 issue。

## 数据源

| 库 | 位置 | 用到的表 |
|---|---|---|
| `sisyphus` | orchestrator 主库 | `artifact_checks`, `stage_runs`, `verifier_decisions`, `req_state` |

`bkd_snapshot` / `event_log` 不在本看板里，在 [queries/alert-*.sql](queries/alert-*.sql) 那套看板里。

新增表定义：
- [orchestrator/migrations/0004_stage_runs.sql](../orchestrator/migrations/0004_stage_runs.sql)：每次 stage 调用的起止、agent、模型、token、结果
- [orchestrator/migrations/0005_verifier_decisions.sql](../orchestrator/migrations/0005_verifier_decisions.sql)：verifier 判决记录，带后续回填的 actual_outcome

**Metabase 接入**：`Admin → Databases → Add database`，Engine 选 PostgreSQL，Host 指向
`sisyphus-postgresql.sisyphus.svc.cluster.local`，Database 填 `sisyphus`，账号和现有
sisyphus_obs 一致（见 `values/postgresql.yaml`）。后文 Question 都基于这个数据源。

## 5 + 8 条 SQL → 13 个 Question

M7 原 5 条（Q1–Q5）基于 `artifact_checks`。M14e 追加 8 条（Q6–Q13）基于 `stage_runs` / `verifier_decisions`。

所有 SQL 在 [queries/sisyphus/](queries/sisyphus/)。Metabase 里 **New Question → SQL 模式
→ 选 sisyphus 数据源 → 粘贴 SQL → Save**。

### Q1. Stuck checks（钻牛角尖的 REQ × stage）

- **SQL**：[queries/sisyphus/01-stuck-checks.sql](queries/sisyphus/01-stuck-checks.sql)
- **Visualization**：Table（列顺序：`req_id, stage, fail_count, total_count, last_fail, last_cmd, last_stderr`）
- **条件格式**：`fail_count ≥ 5` 行标红
- **过滤器**：无（SQL 已限 24h）
- **人工介入阈值**：看板出现 ≥ 1 行即应人工介入。若 fail_count ≥ 5 视为高优。

### Q2. Check duration anomaly（慢异常）

- **SQL**：[queries/sisyphus/02-check-duration-anomaly.sql](queries/sisyphus/02-check-duration-anomaly.sql)
- **Visualization**：Table（按 `ratio DESC` 排序）
- **次选 Visualization**：Bar chart，X = `checked_at` 按小时 bin，Y = COUNT，按 stage 分组
- **人工介入阈值**：单日 ≥ 3 条同 stage 慢异常 → 先看外部依赖，其次重算该 stage 的 P95 基线。

### Q3. Stage success rate（7d 各 stage 通过率）

- **SQL**：[queries/sisyphus/03-stage-success-rate.sql](queries/sisyphus/03-stage-success-rate.sql)
- **Visualization**：Bar chart
  - X = `stage`
  - Y = `pass_rate_pct`
  - 条件格式：红（< 50%）/ 黄（50–80%）/ 绿（> 80%）
- **次选 Visualization**：Table 展示完整列，放看板右下角
- **人工介入阈值**：`pass_rate_pct < 50%` 的 stage 进入待改进清单；突降 ≥ 20pp（和上周同查询对比）应立刻排查。

### Q4. Fail kind distribution（失败原因分桶）

- **SQL**：[queries/sisyphus/04-fail-kind-distribution.sql](queries/sisyphus/04-fail-kind-distribution.sql)
- **Visualization**：Pie chart，Dimension = `fail_kind`，Metric = `fail_count`
- **次选 Visualization**：Row chart（横向柱）按 `fail_count DESC`，额外展示 `sample_stderr` 作为 Tooltip
- **人工介入阈值**：
  - `other` 占比 > 40% → 补关键词桶（改 SQL 里的 CASE WHEN）
  - `schema` 桶飙升（周比 > 50% 上涨）→ 契约变更未同步，优先拉齐
  - `timeout` 桶飙升 → 外部依赖或网络问题

### Q5. Active REQ overview（在飞 REQ + 卡多久）

- **SQL**：[queries/sisyphus/05-active-req-overview.sql](queries/sisyphus/05-active-req-overview.sql)
- **Visualization**：Table（列顺序：`req_id, state, last_stage, last_passed, stuck_min, recent_fail_24h, bugfix_rounds, last_checked_at, last_cmd`）
- **条件格式**：
  - `stuck_min > 30` 且 `last_passed = false` → 整行标红
  - `bugfix_rounds ≥ 3` → 该列底色黄
- **人工介入阈值**：
  - 单 REQ 红行持续 30 min 未变化 → 介入
  - 同一 `last_stage` 同时有 ≥ 3 条 REQ 红行 → 该 stage 整体劣化，查 checker / agent

## M14e 质量指标（Q6–Q13）

基于 `stage_runs` / `verifier_decisions`。埋点由调用方逐步推广 —— 未埋点的 stage 不会出现在看板里，先做优先级高的 stage。

### Q6. Stage success rate by week（周度通过率）

- **SQL**：[queries/sisyphus/06-stage-success-rate-by-week.sql](queries/sisyphus/06-stage-success-rate-by-week.sql)
- **Visualization**：Line chart，X = `week_start`，Y = `pass_rate_pct`，按 `stage` 分组
- **人工介入阈值**：
  - 单周某 stage `pass_rate_pct < 50%` 且样本 ≥ 10 → 进待改进清单
  - 周环比下降 ≥ 20pp → 立即排查

### Q7. Stage duration percentiles（耗时分位）

- **SQL**：[queries/sisyphus/07-stage-duration-percentiles.sql](queries/sisyphus/07-stage-duration-percentiles.sql)
- **Visualization**：Table（列顺序：`stage, runs, p50, p90, p95, max_duration, avg_duration`），按 `p95 DESC` 排序
- **人工介入阈值**：
  - `p95 / p50 ≥ 5` → 长尾严重，查个别 outlier
  - 同 stage 周环比 p95 上涨 ≥ 50% → 外部依赖退化

### Q8. Verifier decision accuracy（判决准确率）

- **SQL**：[queries/sisyphus/08-verifier-decision-accuracy.sql](queries/sisyphus/08-verifier-decision-accuracy.sql)
- **Visualization**：Bar chart，X = `decision_action`，Y = `accuracy_pct` 和 `high_conf_acc_pct` 双系列
- **人工介入阈值**：
  - 某 action 准确率 < 60% 且样本 ≥ 20 → verifier prompt 调优
  - `high_conf_acc_pct < accuracy_pct` → confidence 标定失准，需校准

### Q9. Fix success rate by fixer（修复成功率）

- **SQL**：[queries/sisyphus/09-fix-success-rate-by-fixer.sql](queries/sisyphus/09-fix-success-rate-by-fixer.sql)
- **Visualization**：Table（按 `success_rate_pct ASC` 排序），条件格式：< 50% 标红
- **人工介入阈值**：
  - 某 (fixer, scope) 成功率 < 50% 且样本 ≥ 10 → 换策略或加强 prompt
  - `scope=stage-wide` 显著低于 `file` → 过度放大修复范围

### Q10. Token cost by REQ（REQ 成本榜）

- **SQL**：[queries/sisyphus/10-token-cost-by-req.sql](queries/sisyphus/10-token-cost-by-req.sql)
- **Visualization**：Table（按 `total_tokens DESC` 排序，展示 `est_cost_usd`）
- **人工介入阈值**：
  - 单 REQ `total_tokens > 2M` → 大概率卡在 bugfix loop
  - `token_in / token_out > 20:1` → prompt 膨胀 / 上下文设计问题
- **注意**：价目 `$3/M input + $15/M output` 固化在 SQL 里，后续换模型时改 SQL 而非改表

### Q11. Parallel dev speedup（并行加速比）

- **SQL**：[queries/sisyphus/11-parallel-dev-speedup.sql](queries/sisyphus/11-parallel-dev-speedup.sql)
- **Visualization**：Table（按 `started_at DESC`），列 `req_id, branches, speedup_ratio, max_duration_sec, sum_duration_sec`
- **人工介入阈值**：
  - `speedup_ratio / branches < 0.5` → 并行收益差，查锁/瓶颈
  - `speedup_ratio ≈ 1` → 串行化了

### Q12. Bugfix loop anomaly（卡死异常）

- **SQL**：[queries/sisyphus/12-bugfix-loop-anomaly.sql](queries/sisyphus/12-bugfix-loop-anomaly.sql)
- **Visualization**：Table（按 `fail_count DESC`），条件格式：`fail_count ≥ 5` 整行标红
- **人工介入阈值**：
  - `fail_count ≥ 5` 即异常，人工介入
  - `fail_count ≥ 8` 建议直接 escalate（转人工兜底）

### Q13. Watchdog escalate frequency（escalate 频率）

- **SQL**：[queries/sisyphus/13-watchdog-escalate-frequency.sql](queries/sisyphus/13-watchdog-escalate-frequency.sql)
- **Visualization**：Line chart，X = `day`，Y = `total_escalates`，按 `stage` 分组
- **人工介入阈值**：
  - 日合计 ≥ 5 或日环比 ×2 → 系统整体退化
  - 某 stage 连续 3 天有 escalate → 该 stage 策略要重新设计

## Fixer Audit 看板（Q14–Q16）

基于 `verifier_decisions.audit` JSONB 列（migration 0006）。只有 after-fix 二次 verify 才有 audit 数据（`WHERE audit IS NOT NULL`）。

### Q14. Fixer audit verdict trend（verdict 趋势）

- **SQL**：[queries/sisyphus/14-fixer-audit-verdict-trend.sql](queries/sisyphus/14-fixer-audit-verdict-trend.sql)
- **Visualization**：Stacked bar chart，X = `day`，Y = `n`，按 `verdict` 分组
  - 颜色：`legitimate` = 绿；`test-hack` / `code-lobotomy` = 红；`spec-drift` = 橙；`unclear` = 灰
- **人工介入阈值**：
  - `test-hack` 或 `code-lobotomy` 占比 ≥ 20% → fixer prompt 质量退化，检查 bugfix.md.j2
  - 连续 3 天有 non-legitimate verdict 且样本 ≥ 5 → 专项 review

### Q15. Suspicious pass decisions（可疑通过决策）

- **SQL**：[queries/sisyphus/15-suspicious-pass-decisions.sql](queries/sisyphus/15-suspicious-pass-decisions.sql)
- **Visualization**：Table（列顺序：`req_id, stage, verdict, flags, made_at`）
- **说明**：action=pass 但 audit verdict 不是 legitimate（= "通过了但可疑"）。用于人工复盘 fixer 是否作弊绕过测试。
- **人工介入阈值**：
  - 出现任意一行 verdict=test-hack 且 action=pass → 立即 review 该 REQ 的 PR diff
  - 同一 req_id 反复出现 → 该 REQ 的 fixer 策略需要人工介入

### Q16. Fixer file category breakdown（文件分类分布）

- **SQL**：[queries/sisyphus/16-fixer-file-category-breakdown.sql](queries/sisyphus/16-fixer-file-category-breakdown.sql)
- **Visualization**：Stacked bar chart，X = `week`，Y = `*_changes`，4 个系列（src/tests/spec/config）
- **人工介入阈值**：
  - `test_changes > src_changes` 且 `test_changes ≥ 5` → fixer 改测试比改代码还多，可疑
  - `spec_changes` 持续增长 → fixer 在改 spec 迎合实现，spec-drift 告警

## 机械 checker silent-pass（Q18）

`spec_lint` / `dev_cross_check` / `staging_test` / `pr_ci_watch` 各自源代码里都有
`refusing to silent-pass` guard（empty source / `ran=0` / `no-gha-checks-ran`），
设计上"零信号即 fail"。Q18 是**事后兜底**：从 `artifact_checks` 已落表的样本里
反查"通过了但其实没干活"的记录，万一 guard 失效或脚本被改坏可以从指标层捞回来。

跟 Q14–Q16 的 fixer audit（agent 主观作弊）正交：那套针对 agent 的修复是否合规；
本节针对**机械层**自身的"假阳性 pass"，互不重叠。

### Q18. Silent-pass detector（机械 checker 沉默通过）

- **SQL**：[queries/sisyphus/18-silent-pass-detector.sql](queries/sisyphus/18-silent-pass-detector.sql)
- **Visualization**：Table（列顺序：`req_id, stage, silent_pass_kind, duration_sec, p50_sec, ratio, evidence, cmd, checked_at`），按 `silent_pass_kind` 升序 + `checked_at` 降序排
  - 条件格式：`silent_pass_kind ∈ ('guard-leak', 'no-gha-pass')` 整行标红
- **三类信号**：
  - `guard-leak` —— stdout/stderr 命中 `refusing to silent-pass` 但 `passed=true`：checker 内部 guard 触发了告警 line 但 exit code 还是 0，**checker 实现 bug**
  - `no-gha-pass` —— pr_ci_watch 留了 `no-gha-checks-ran` 但 `passed=true`：`_classify` 返回 `no-gha` 时本应 fail，出现说明分类逻辑被改坏
  - `too-fast` —— `duration_sec < 0.2 × 同 stage 7d P50（passed=true 样本）`：跑得比中位数快 5×，多半是脚本短路（`for ... in /workspace/source/*` 循环没进 body），和 Q2（慢异常）对称
- **人工介入阈值**：
  - 任意 `guard-leak` 或 `no-gha-pass` 行 → 立即介入，检查 checker 源码
  - `too-fast` 单日 ≥ 3 条同 stage → 重算 P50 基线 / 查脚本短路（per-repo for 循环 / git fetch 静默失败）
- **基线说明**：P50 用同 stage 7d `passed=true` 样本，`HAVING COUNT(*) ≥ 20` 跳过样本不足的 stage（避免 too-fast 误报）。`guard-leak` / `no-gha-pass` 不依赖基线，无样本要求。

## 看板布局

推荐两列布局（Metabase Dashboard，gridsize 18×? 自适应）：

```
┌──────────────────────────────┬──────────────────────────────┐
│  Q5. Active REQ overview     │  Q1. Stuck checks            │
│  (table, 全宽左列)            │  (table)                     │
├──────────────────────────────┼──────────────────────────────┤
│  Q3. Stage success rate      │  Q4. Fail kind distribution  │
│  (bar chart)                 │  (pie chart)                 │
├──────────────────────────────┴──────────────────────────────┤
│  Q2. Check duration anomaly                                 │
│  (table 全宽，按需下钻)                                       │
└─────────────────────────────────────────────────────────────┘
```

Q5 放显眼处（运维最先看）；Q1 右上辅证；Q3/Q4 是周度趋势；Q2 最细粒度，出问题时下钻。

**M14e 质量看板（建议单独一个 Dashboard）**：

```
┌──────────────────────────────┬──────────────────────────────┐
│  Q12. Bugfix loop anomaly    │  Q13. Watchdog escalate      │
│  (table, 红行优先)            │  (line chart)                │
├──────────────────────────────┼──────────────────────────────┤
│  Q6. Stage success by week   │  Q8. Verifier accuracy       │
│  (line chart)                │  (bar chart)                 │
├──────────────────────────────┼──────────────────────────────┤
│  Q9. Fix success by fixer    │  Q11. Parallel dev speedup   │
│  (table)                     │  (table)                     │
├──────────────────────────────┼──────────────────────────────┤
│  Q7. Stage duration P95      │  Q10. Token cost by REQ      │
│  (table)                     │  (table)                     │
└──────────────────────────────┴──────────────────────────────┘
```

Q12/Q13 是"此刻在出事吗"，放最上；Q6/Q8 是周度趋势；Q9/Q11 是策略有效性；Q7/Q10 是最细粒度下钻。

**Fixer Audit 看板（建议和 M14e 放同一 Dashboard 底部）**：

```
┌──────────────────────────────┬──────────────────────────────┐
│  Q14. Audit verdict trend    │  Q15. Suspicious pass        │
│  (stacked bar)               │  (table，红行优先)            │
├──────────────────────────────┴──────────────────────────────┤
│  Q16. File category breakdown（stacked bar 全宽）            │
└─────────────────────────────────────────────────────────────┘
```

**机械 checker silent-pass 看板（建议挂 M7 看板底部，跟 Q1 同一界面方便对照）**：

```
┌─────────────────────────────────────────────────────────────┐
│  Q18. Silent-pass detector（机械 checker 沉默通过）           │
│  (table 全宽，guard-leak / no-gha-pass 行标红)                │
└─────────────────────────────────────────────────────────────┘
```

## 刷新频率

M7 看板：
- Q5 / Q1：每 1 分钟（运维实时盯）
- Q3 / Q4：每 5 分钟
- Q2：每 5 分钟

M14e 质量看板：
- Q12 / Q13：每 5 分钟（异常实时发现）
- Q6 / Q8 / Q9 / Q11：每 1 小时（趋势类，不频繁）
- Q7 / Q10：每 1 小时

机械 checker silent-pass 看板：
- Q18：每 5 分钟（和 Q2 同一节奏；guard-leak / no-gha-pass 出现等于"checker 实现有 bug"，越早发现越好）

Metabase Question 设置 `Results cache TTL`：Q5/Q1 设 30s，Q2/Q3/Q4/Q12/Q13/Q18 设 120s，其余设 1800s。

## 告警（后续 issue，不在 M7 范围）

本 issue 明确不接通知渠道。后续新 issue 把以下 Question 升成 Metabase Alert：

- Q1：`row count > 0 AND max(fail_count) ≥ 5` → Lark
- Q5：`row count > 0` WHERE `stuck_min > 30 AND last_passed = false` → Lark
- Q3：新建 Question "任何 stage pass_rate_pct < 50%" → Lark
- Q18：`row count > 0` WHERE `silent_pass_kind IN ('guard-leak','no-gha-pass')` → Lark（这两类是 checker 实现 bug，应当永远没行）

阈值都可在 Metabase UI 里直接改，不用动 SQL。

## 维护须知

- 4 条桶匹配是启发式，日常看 `other` 占比：若持续 > 40%，改 04 SQL 的 CASE WHEN 补关键词
- P95 基线在 02 SQL 里内联计算，stage 样本 < 20 次时跳过，避免小样本噪声
- 05 SQL 的 `state NOT IN ('done','escalated')` 列表要和 [state.py](../orchestrator/src/orchestrator/state.py) 的终态保持一致

### M14e 埋点规范（逐步推广）

- stage 入口调 `store/stage_runs.insert_stage_run(...)` 拿 `run_id`
- stage 出口调 `store/stage_runs.update_stage_run(run_id, outcome=..., fail_reason=...)`
- verifier 判决时调 `store/verifier_decisions.insert_decision(...)`
- 后续事实验证明朗时调 `store/verifier_decisions.mark_correct(decision_id, ...)` 回填
- token 数据从 BKD logs 抽，额外定时 job 批量回填（本 PR 不做）
- 不强制每个 action 埋点 —— 未埋的 stage 自然不出现在 Q6–Q13 看板里，先做高价值 stage
