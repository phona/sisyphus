# Sisyphus checker 可观测性看板（M7）

围绕 `artifact_checks` 表（M1 PR #3 引入，定义在 [orchestrator/migrations/0003_artifact_checks.sql](../orchestrator/migrations/0003_artifact_checks.sql)）的 Metabase 看板设计。

**目的**：让运维"一眼看出 sisyphus 内置 checker 在哪些 REQ / stage 上钻牛角尖"。

**不接通知渠道**（飞书 / 邮件）。此版本只输出 SQL + Question 配置，人工看板兜底。通知通道另起 issue。

## 数据源

| 库 | 位置 | 用到的表 |
|---|---|---|
| `sisyphus` | orchestrator 主库 | `artifact_checks`, `req_state` |

`bkd_snapshot` / `event_log` 不在本看板里，在 [queries/alert-*.sql](queries/alert-*.sql) 那套看板里。

**Metabase 接入**：`Admin → Databases → Add database`，Engine 选 PostgreSQL，Host 指向
`sisyphus-postgresql.sisyphus.svc.cluster.local`，Database 填 `sisyphus`，账号和现有
sisyphus_obs 一致（见 `values/postgresql.yaml`）。后文 Question 都基于这个数据源。

## 5 条 SQL → 5 个 Question

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

## 刷新频率

- Q5 / Q1：每 1 分钟（运维实时盯）
- Q3 / Q4：每 5 分钟
- Q2：每 5 分钟

Metabase Question 设置 `Results cache TTL`：Q5/Q1 设 30s，其余设 120s。

## 告警（后续 issue，不在 M7 范围）

本 issue 明确不接通知渠道。后续新 issue 把以下 Question 升成 Metabase Alert：

- Q1：`row count > 0 AND max(fail_count) ≥ 5` → Lark
- Q5：`row count > 0` WHERE `stuck_min > 30 AND last_passed = false` → Lark
- Q3：新建 Question "任何 stage pass_rate_pct < 50%" → Lark

阈值都可在 Metabase UI 里直接改，不用动 SQL。

## 维护须知

- 4 条桶匹配是启发式，日常看 `other` 占比：若持续 > 40%，改 04 SQL 的 CASE WHEN 补关键词
- P95 基线在 02 SQL 里内联计算，stage 样本 < 20 次时跳过，避免小样本噪声
- 05 SQL 的 `state NOT IN ('done','escalated')` 列表要和 [state.py](../orchestrator/src/orchestrator/state.py) 的终态保持一致
