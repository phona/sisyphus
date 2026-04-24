# Sisyphus 可观测性设计

> 兜底 + 分析 + **改进驱动** 的最小可观测系统。
> Schema / 部署 / 运维见 [observability/README.md](../observability/README.md)。
> 完整 SQL + Metabase 看板配置见 [observability/sisyphus-dashboard.md](../observability/sisyphus-dashboard.md)。

## 目标（排序）

1. **实时兜底**：不让已知设计缺陷（重复 fanout / verifier 钻牛角尖 / stuck session / runaway loop）静默产生脏状态
2. **离线分析**：知道系统跑得怎么样（REQ 通过率、token 成本、各 agent 质量、失败模式分布、verifier 决策准确率）
3. **改进驱动**（关键）：每次改 prompt / 阈值 / verifier 决策协议后，**能用数据测出来效果**。没有这条，观测只是摆设

三个目标共用同一份数据 pipeline。

## 核心原则

| # | 原则 | 理由 |
|---|---|---|
| 1 | **BKD 是主账本，Postgres 是 orchestrator 的日记** | 不复制 BKD 已有的状态，只存 BKD 看不见的（路由决策、checker 结果、verifier 决策、人工介入、token 成本） |
| 2 | **观测不能成为新故障源** | 写表用独立连接池 + best-effort try/except；observability 写挂掉不阻塞主流程 |
| 3 | **Fail-closed > dashboard** | 检测结果必须能反馈到 verifier / watchdog（通过 BKD tag 或直接 emit Event），不只是出图 |
| 4 | **orchestrator 负责采集，Metabase 负责分析** | orchestrator 的 store/ 模块负责 INSERT；分析逻辑全在 SQL + Metabase，不写额外服务 |
| 5 | **每次改动可追溯** | 改了 prompt / 阈值，事件要带 `config_version`，才能前后对比 |
| 6 | **先被动报告，再主动干预** | 新规则先只看图；攒 2 周数据、误报率 < 5% 才升级到自动 cancel / escalate |
| 7 | **不提前优化** | Postgres + Metabase 够用就够用，OTel / Prometheus / Loki 用不上不引 |

## 为什么不是 OpenTelemetry / Prometheus / Loki

Sisyphus 的数据形状是**业务事件**（结构化、低频、高基数），不是 metric（数值时序低基数）或 trace（分布式调用链）或 log（半结构化文本流）。

| 候选 | 淘汰理由 |
|---|---|
| Prometheus | 事件数据高基数（req_id 每次都新值）会撑爆 Prometheus；PromQL 答不了"REQ-29 卡哪了"这种状态查询 |
| OpenTelemetry + Tempo | 要跨 orchestrator / BKD / agent session 埋 trace context，工作量大；BKD 自己的 session log 已经是 trace |
| Loki / ELK | 日志已经各自在 orchestrator / BKD / GH 里，集中化是冗余；结构化查询 SQL 更好 |
| ClickHouse | 年级别 < 100K 行，杀鸡用牛刀 |

**Postgres + Metabase** 在 "小规模 + 事件形状 + SQL 聚合 + 自助 BI + 运维预算低" 这 5 个约束下代价最小。

## 架构

```
                    ┌──────────────┐
                    │ BKD webhook  │
                    └──────┬───────┘
                           ▼
   ┌──────────────────────────────────────────────────┐
   │  orchestrator (Python)                           │
   │   webhook.py → router → engine → action          │
   │     │                                            │
   │     ├── store/event_log    ← INSERT 每条决策     │
   │     ├── store/req_state     ← CAS state          │
   │     ├── store/stage_runs    ← M14e stage 起止    │
   │     ├── store/verifier_decisions ← M14e          │
   │     ├── store/artifact_checks ← M7 checker 结果  │
   │     │                                            │
   │   snapshot.py (cron 每 5 min)                    │
   │     └── BKD list-issues → upsert bkd_snapshot    │
   └──────────────────────────────────────────────────┘
                                 ▼
            ┌─────────────────────────────────┐
            │  Postgres (sisyphus 库)         │
            │  - event_log (append-only)      │
            │  - req_state (CAS)              │
            │  - stage_runs (M14e)            │
            │  - verifier_decisions (M14e)    │
            │  - artifact_checks (M7)         │
            │  - bkd_snapshot (覆盖式)        │
            │  - config_version               │
            │  - improvement_log              │
            └──────────┬──────────┬───────────┘
                       │          │
           ┌───────────▼─┐   ┌────▼─────────┐
           │ Metabase    │   │ Metabase     │
           │ 13 看板     │   │ 4 Alert SQL  │
           │ (Q1-Q13)    │   │ (定时跑)     │
           └─────────────┘   └──┬───────────┘
                                │ 命中
                                ▼
                     ┌────────────────────┐
                     │ 通知（飞书 webhook）│
                     │   ↓ 升级           │
                     │ orchestrator emit  │
                     │ → BKD cancel       │
                     │ → escalate state   │
                     └────────────────────┘
```

**职责边界**：

- **orchestrator**：webhook 接收、状态机、调 BKD / GitHub / kubectl，所有写表也由它的 `store/` 模块负责
- **Postgres**：事件账本 + 当前状态 + 配置版本 + 改进日志
- **Metabase**：自助 BI + 定时 SQL + 阈值告警 webhook
- **人**：看板看趋势，改 prompt / verifier 决策协议，写 improvement_log 假设

## 数据模型

7 张主表 + view。完整 DDL 见 [observability/schema.sql](../observability/schema.sql) 和 [orchestrator/migrations/](../orchestrator/migrations/) 0001~0005。

### event_log（append-only）

orchestrator 每条决策都写一行：

- `kind`：`webhook.received` / `router.decision` / `gate.check` / `action.executed` / `action.failed` / `dedup.hit` / `check.violation` / `human.approval` / `session.cost`
- 业务维度：`req_id / stage / issue_id / parent_*`
- 解析后的 tag：`tags[] / round / target`
- kind 特定列：`router_action / duration_ms / exit_code / token_in/out / ...`
- 追溯：`config_version / extras JSONB`

**索引**：`(req_id, ts) / (issue_id, ts) / (kind, ts) / tags GIN`

### artifact_checks（M3/M7）

每次机械 checker 跑（staging_test / pr_ci_watch）一行结果，带 `passed / exit_code / stdout_tail / stderr_tail / duration_sec / cmd / reason`。

### stage_runs（M14e）

每次 stage 调用的起止 + agent + 模型 + token + 结果。supports Q3/Q6/Q7/Q10/Q11 看板。

### verifier_decisions（M14e + fixer-audit）

每条 verifier-agent 决策记录：`req_id / stage / trigger / decision_action / fixer / confidence / reason / actual_outcome`。`actual_outcome` 后续回填，让 Q8 算 verifier 准确率。

**audit 字段（migration 0006）**：after-fix 二次 verify 时，verifier 附带一份 `audit JSONB`，包含：
- `diff_summary`（str）：PR diff 统计摘要，格式 `"src=+12/-3 tests=+8/-0"`
- `verdict`（enum）：`legitimate` / `test-hack` / `code-lobotomy` / `spec-drift` / `unclear`
- `red_flags`（list[str]）：检测到的可疑行为（如删 assert、加 t.Skip、hardcode 期望值）
- `files_by_category`（dict）：按 src / tests / spec / config 分类的改动文件数

first-time verify（非 after-fix）`audit = NULL`，向后兼容老数据。Q14/Q15/Q16 看板均有 `WHERE audit IS NOT NULL` 过滤。

**红旗 patterns（verifier 审计要点）**：
- 删 `assert` / `require` / `t.Error` / `t.Fatal` 调用 → `test-hack` 信号
- 加 `t.Skip(` / `//nolint` / `testing.Short()` 绕过用例 → `test-hack` 信号
- hardcode 期望值（`want = "hardcoded"` 而非读真实结果）→ `test-hack` 信号
- `tests/` 有改动但 `src/` 没有实质性对应改动 → `test-hack` 信号
- `openspec/` 被改来迎合现有实现 → `spec-drift` 信号
- 业务代码被删/注释/stub 化 → `code-lobotomy` 信号

### bkd_snapshot（覆盖式）

`snapshot.py` 每 5 分钟 `BKD list-issues` → 解析 → upsert。BKD 自己是主账本，这张表只是本地镜像，让 Metabase 能用 SQL 做 `JOIN event_log` 这类聚合。

### config_version

每次改 prompt / verifier 决策协议 / 阈值，插一行：

```sql
INSERT INTO config_version (version_hash, kind, target, git_commit, diff_summary, author)
VALUES ('verifier-pr-ci-v2', 'prompt', 'verifier/pr_ci_fail.md.j2',
        'abc1234567...', '加 retry_checker 提示', 'tc@uzaqfood.com');

UPDATE config_version SET retired_at = now()
WHERE kind='prompt' AND target='verifier/pr_ci_fail.md.j2'
  AND version_hash <> 'verifier-pr-ci-v2' AND retired_at IS NULL;
```

orchestrator 启动时从 git commit 拼当前 version_hash 写进 `event_log.config_version`，让看板能按版本切片对比。

### improvement_log（可持续改进的核心）

把"改了什么 / 为什么改 / 验证结果"落成可查的记录：

```sql
INSERT INTO improvement_log
  (hypothesis, config_version, metric_name, metric_sql, baseline_value, target_value)
VALUES
  ('verifier pr_ci_fail prompt 加 retry_checker 选项，预期 retry 占比 ↑、误判 escalate ↓',
   'verifier-pr-ci-v2',
   'retry_checker_share',
   'SELECT 100.0 * SUM(CASE WHEN decision_action=''retry_checker'' THEN 1 ELSE 0 END) / COUNT(*) FROM verifier_decisions WHERE stage=''pr_ci''',
   5, 25);

-- 2 周后填 observed + verdict
UPDATE improvement_log
SET observed_value = 22, verdict = 'success', closed_at = now()
WHERE id = <id>;
```

Metabase 看板直接读这张表就能看"改进成绩单"。

## 看板与告警

完整 SQL + Metabase Question 配置见 [observability/sisyphus-dashboard.md](../observability/sisyphus-dashboard.md)。

**16 个看板**（[observability/queries/sisyphus/](../observability/queries/sisyphus/)）：

- Q1-Q5（M7，基于 `artifact_checks`）：钻牛角尖 / 慢异常 / stage 通过率 / 失败分桶 / 在飞 REQ
- Q6-Q13（M14e，基于 `stage_runs` + `verifier_decisions`）：
  - Q6 stage 周维度通过率
  - Q7 stage duration P50/P95
  - Q8 verifier 决策准确率（actual_outcome 回填后）
  - Q9 fixer 修复成功率（按 fixer:dev/spec 分）
  - Q10 token 成本
  - Q11 dev 并行加速比（M14d 后启用）
  - Q12 bugfix loop 异常
  - Q13 watchdog escalate 频率
- Q14-Q16（fixer-audit，基于 `verifier_decisions.audit` JSONB）：
  - Q14 fixer audit verdict 趋势（按天，回答"fixer 是否在 hack 测试"）
  - Q15 可疑 pass 决策（audit verdict != legitimate 但 action=pass）
  - Q16 fixer 改动文件分类分布（src/tests/spec/config 周度占比）

**4 条 Alert**（[observability/queries/](../observability/queries/)）：

| Alert | 触发 | 起步动作 | 升级动作 |
|---|---|---|---|
| Layers drift | analyze-done issue 没 layer:* tag | 飞书 | （已废，layers 概念在 v0.2 不再硬编码） |
| Duplicate stage | 同 REQ 同 stage > 1 个活跃 issue | 飞书 | cancel 较新的 |
| Bugfix runaway | fixer 同 stage 连续 ≥ 3 轮 | 飞书 | escalate |
| Stuck session | working 超过 stage SLA | 飞书 | watchdog 已内建（M8）—— 此 alert 是双保险 |

## 可持续改进闭环

```
   ┌──────────────────────────────────────┐
   │  1. 观察异常                         │
   │     Q8 发现 verifier pr_ci_fail      │
   │     一把就 escalate 占 60%          │
   └──────────────┬───────────────────────┘
                  ▼
   ┌──────────────────────────────────────┐
   │  2. 根因诊断                         │
   │     看 verifier_decisions.reason +   │
   │     stderr_tail，找共同模式          │
   └──────────────┬───────────────────────┘
                  ▼
   ┌──────────────────────────────────────┐
   │  3. 生成假设                         │
   │     "verifier 把 GHA 网络抖动        │
   │      误判成 SPEC bug"                │
   └──────────────┬───────────────────────┘
                  ▼
   ┌──────────────────────────────────────┐
   │  4. 实施改动                         │
   │     改 verifier/pr_ci_fail.md.j2     │
   │     加 retry_checker 触发条件        │
   │     → INSERT config_version          │
   │     → INSERT improvement_log 假设    │
   │     → 部署                           │
   └──────────────┬───────────────────────┘
                  ▼
   ┌──────────────────────────────────────┐
   │  5. 效果验证                         │
   │     2 周后跑 metric_sql 拿新数据     │
   │     对比 baseline → 填 verdict       │
   │     回归时 Metabase 自动告警         │
   └──────────────┬───────────────────────┘
                  │
                  └──→ 下一轮
```

关键是 **`config_version` 和 `improvement_log`**：把每次改动和每条指标挂钩，让改进行为本身变得可被度量，才谈得上"可持续"。

## 容量 / 性能估算

| 表 | 日增 | 年增 | 查询 |
|---|---|---|---|
| event_log | ~100 行 × 5 REQ = 500 行 | ~180K | 索引命中 < 10ms |
| artifact_checks | ~20 行 × 5 REQ = 100 行 | ~36K | < 5ms |
| stage_runs | ~10 行 × 5 REQ = 50 行 | ~18K | < 5ms |
| verifier_decisions | ~5 行 × 5 REQ = 25 行 | ~9K | < 1ms |
| bkd_snapshot | 覆盖式，~几千行 | 不增长 | < 5ms |
| config_version | 改一次加一行 | < 100 | < 1ms |
| improvement_log | 每次改动加一行 | < 100 | < 1ms |

**3 年内完全在 Postgres 单实例舒适区**，不需分区、不需归档。10Gi 盘撑 20 年。

## 兜底覆盖范围

本观测系统**兜底**（detect + escalate），不**修复**架构缺陷。修复在 orchestrator/state.py / actions/ 改。

| 缺陷 | 谁兜 | 残留风险 |
|---|---|---|
| 同 REQ 同 stage 重复 fanout | Duplicate stage alert | 秒级检测，但 agent 已烧 token。短期可接受 |
| Action 无事务 / 幂等 key（webhook 重放） | CAS state 自带 + Duplicate stage | 同上 |
| verifier 决策错误（误 escalate / 误 pass） | Q8 准确率看板（actual_outcome 回填） | 只在 batch 复盘时发现，realtime 看不到 |
| stage 卡死 | watchdog (M8) 主动 emit SESSION_FAILED | 完全兜住 |
| runner pod 泄漏 | runner_gc (M10) | 完全兜住 |

**先兜住，再慢慢修**。每个缺陷被观测抓到后，`improvement_log` 里记一条"修复假设"，跟踪到 verdict=success 才算真解决。

## 引用

- [observability/README.md](../observability/README.md) — 部署 + 运维 + 查询速查
- [observability/schema.sql](../observability/schema.sql) — 基础 DDL（pre-M14e）
- [orchestrator/migrations/](../orchestrator/migrations/) — 0001~0005，含 stage_runs / verifier_decisions
- [observability/sisyphus-dashboard.md](../observability/sisyphus-dashboard.md) — 13 看板 + Metabase Question 详细配置
- [observability/queries/](../observability/queries/) — Alert SQL + sisyphus/ 子目录的 13 条看板 SQL
- [values/postgresql.yaml](../values/postgresql.yaml) / [values/metabase.yaml](../values/metabase.yaml) — helm values
- [architecture.md](./architecture.md) — 权威架构（本观测系统是其第三支柱）
- [state-machine.md](./state-machine.md) — 状态机权威（state / event / transition）
