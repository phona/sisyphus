# Sisyphus 可观测性设计

> 兜底 + 分析 + 改进驱动 的最小可观测系统。
> Schema / 部署 / 运维见 [observability/README.md](../observability/README.md)。

## 目标（排序）

1. **实时兜底**：不让已知设计缺陷（layers 漂移 / 重复 fanout / bugfix 暴走 / stuck session）静默产生脏状态
2. **离线分析**：知道系统跑得怎么样（REQ 通过率、token 成本、各 agent 质量、失败模式分布）
3. **改进驱动**（关键）：每次改 prompt / Router 规则 / 熔断阈值后，**能测出来效果**。没有这条，观测只是摆设

三个目标共用同一份数据 pipeline。

## 核心原则

| # | 原则 | 理由 |
|---|---|---|
| 1 | **BKD 是主账本，Postgres 是 n8n 的日记** | 不复制 BKD 已有的状态，只存 BKD 看不见的（Router 决策、兜底命中、人工介入、成本）|
| 2 | **观测不能成为新故障源** | 所有 tap 节点 `timeout=2s` + `onError=continueRegularOutput`；observer 挂掉不阻塞业务 |
| 3 | **Fail-closed > dashboard** | 检测结果必须能反馈到 Router 决策（通过 BKD tag / GH incident），不只是出图 |
| 4 | **n8n 负责粘合，Metabase 负责分析** | n8n 里只加 tap 节点；分析逻辑全在 SQL + Metabase，不写服务代码 |
| 5 | **每次改动可追溯** | 改了 prompt 或 Router 规则，事件要带 `config_version`，才能前后对比 |
| 6 | **先被动报告，再主动干预** | 规则先只发 Lark；攒 2 周数据、误报率 < 5% 才升级自动 cancel / escalate |
| 7 | **不提前优化** | Postgres + Metabase 够用就够用，OTel / Prometheus / Loki 用不上不引 |

## 为什么不是 OpenTelemetry / Prometheus / Loki

Sisyphus 的数据形状是**业务事件**（结构化、低频、高基数），不是 metric（数值时序低基数）或 trace（分布式调用链）或 log（半结构化文本流）。

| 候选 | 淘汰理由 |
|---|---|
| Prometheus | 事件数据高基数（reqId 每次都新值）会撑爆 Prometheus；PromQL 答不了"REQ-10 卡哪了"这种状态查询 |
| OpenTelemetry + Tempo | 要跨 n8n / BKD / agent session 埋 trace context，工作量大；n8n 自己的 execution history 已经是 trace |
| Loki / ELK | 日志已经各自在 n8n / BKD / GH 里，集中化是冗余；结构化查询 SQL 更好 |
| ClickHouse | 年级别 < 100K 行，杀鸡用牛刀 |

**Postgres + Metabase** 在 "小规模 + 事件形状 + SQL 聚合 + 自助 BI + 运维预算低" 这 5 个约束下代价最小。

## 架构

```
                     ┌──────────────┐
                     │ BKD webhook  │
                     └──────┬───────┘
                            ▼
  ┌──────────────────────────────────────────────┐
  │                    n8n                       │
  │  ┌─────────┐   ┌────────┐   ┌──────────┐     │
  │  │ Hook +  │──▶│ Router │──▶│  Action  │──▶ BKD/GH/Lark
  │  │ Ctx     │   │ (JS)   │   │  Nodes   │     │
  │  └────┬────┘   └───┬────┘   └─────┬────┘     │
  │       │            │              │          │
  │  ┌────▼────────────▼──────────────▼────┐     │
  │  │  tap: Postgres Insert × 5           │     │
  │  └────────────────┬────────────────────┘     │
  │                                              │
  │  ┌─────────────────────────┐                 │
  │  │ BKD sync cron (每 5 min)│──┐              │
  │  │ list-issues → upsert    │  │              │
  │  └─────────────────────────┘  │              │
  └───────────────────────────────┼──────────────┘
                                  ▼
            ┌─────────────────────────────┐
            │     Postgres                │
            │ - event_log (append-only)   │
            │ - bkd_snapshot (覆盖式)     │
            │ - config_version           │
            │ - improvement_log          │
            └──────────┬────────┬─────────┘
                       │        │
           ┌───────────▼─┐   ┌──▼──────────┐
           │ Metabase    │   │ Metabase    │
           │ 5 看板      │   │ 4 Alert     │
           │ (人看)      │   │ (定时 SQL)  │
           └─────────────┘   └──┬──────────┘
                                │ 命中
                                ▼
                     ┌────────────────────┐
                     │ Lark webhook       │
                     │   │                │
                     │   ▼（后续升级）    │
                     │ n8n webhook        │
                     │   → BKD cancel     │
                     │   → GH incident    │
                     │   → 加 tag blocking│
                     └────────────────────┘
```

**职责边界**：

- **n8n**：webhook 接收、MCP session、调 BKD/GH/Lark、HTTP 重试、credential、tap 写入、BKD 定时镜像。**不做聚合、不做决策** 以外的事
- **Postgres**：事件账本 + 当前状态镜像 + 配置版本 + 改进日志
- **Metabase**：自助 BI + 定时 SQL + 阈值告警 webhook
- **人**：看板看趋势，改 prompt / Router，写 improvement_log 假设

## 数据模型

4 张表 + 4 个 view。完整 DDL 见 [observability/schema.sql](../observability/schema.sql)。

### event_log（append-only）

n8n tap 推上来的事件流。只记 BKD 看不见的：

- `kind`：`webhook.received` / `router.decision` / `gate.check` / `action.executed` / `action.failed` / `dedup.hit` / `check.violation` / `human.approval` / `session.cost`
- 业务维度：`req_id / stage / issue_id / parent_*`
- 解析后的 tag：`tags[] / layer[] / round / target`
- kind 特定列：`router_action / duration_ms / exit_code / token_in/out / ...`
- 追溯：`config_version / n8n_exec_id / extras JSONB`

**索引**：`(req_id, ts) / (issue_id, ts) / (kind, ts) / tags GIN`

### bkd_snapshot（覆盖式）

n8n cron 每 5 分钟 `list-issues` → 解析 SSE → flatten → `INSERT ... ON CONFLICT DO UPDATE`。

BKD 自己是主账本，这张表只是本地镜像，让 Metabase 能用 SQL 做 `GROUP BY stage`、`JOIN event_log` 这类聚合查询。

### config_version

每次改 Router / agent prompt / 熔断阈值，插一行：

```sql
INSERT INTO config_version (version_hash, kind, target, git_commit, diff_summary, author)
VALUES ('prompt-ctrtest-v2', 'prompt', 'contract-test-agent',
        'abc1234567...', '加 oneOf schema 处理指令', 'weifashi@jbcnet.co.jp');

UPDATE config_version SET retired_at = now()
WHERE kind='prompt' AND target='contract-test-agent'
  AND version_hash <> 'prompt-ctrtest-v2' AND retired_at IS NULL;
```

n8n Router / action 节点开头从 workflow variable 读当前 version_hash，写进 event_log.config_version。

### improvement_log（可持续改进的核心）

把"改了什么 / 为什么改 / 验证结果"落成可查的记录：

```sql
INSERT INTO improvement_log
  (hypothesis, config_version, metric_name, metric_sql, baseline_value, target_value)
VALUES
  ('改 contract-test prompt 加 oneOf 处理指令，预期一把过率 ↑',
   'prompt-ctrtest-v2',
   'contract_test_first_pass_rate',
   'SELECT first_pass_rate_pct FROM agent_quality WHERE agent_role=''contract-spec''',
   40, 65);

-- 2 周后填 observed + verdict
UPDATE improvement_log
SET observed_value = 63, verdict = 'success', closed_at = now()
WHERE id = <id>;
```

Metabase 看板直接读这张表就能看"改进成绩单"。

## 写入路径：5 个 tap

| tap | n8n 位置 | kind | 关键字段 |
|---|---|---|---|
| tap1 | `[ENTRY] Ctx 提取` 后 | `webhook.received` | tags / stage / issue_id |
| tap2 | `Router` 节点后 | `router.decision` | router_action / router_reason / config_version |
| tap3 | 每个 action HTTP 节点的成功输出 | `action.executed` | stage / duration_ms / status_code |
| tap4 | 每个 action HTTP 节点的 error 输出 | `action.failed` | error_msg / status_code |
| tap5 | `[SPG] Gate check` 后 | `gate.check` | extras: {passedCount, expectedCount, allReady} |

### tap 节点模板

n8n Postgres Insert 节点配置（以 tap2 为例）：

```yaml
type: n8n-nodes-base.postgres
operation: insert
schema: public
table: event_log
columns: "kind,req_id,stage,issue_id,tags,router_action,router_reason,config_version,n8n_exec_id,extras"
values:
  kind: router.decision
  req_id: ={{ $json.params.reqId }}
  stage: ={{ $('Router').first().json.params.parentStage }}
  issue_id: ={{ $('[ENTRY] Ctx 提取').first().json.issueId }}
  tags: ={{ $('[ENTRY] Ctx 提取').first().json.tags }}
  router_action: ={{ $json.action }}
  router_reason: ={{ $json.reason || '' }}
  config_version: ={{ $vars.ROUTER_VERSION }}
  n8n_exec_id: ={{ $execution.id }}
  extras: ={{ JSON.stringify($json.params) }}
timeout: 2000
onError: continueRegularOutput
```

**关键**：`timeout=2000` + `onError=continueRegularOutput`，tap 挂掉不阻塞业务。

### BKD sync workflow

新建一个 n8n workflow：

```
Schedule (cron: */5 * * * *)
  ↓
BKD list-issues (limit=200)
  ↓
Code node: parse SSE → flatten issues[] (复用 router/ 下的 parseSse 逻辑)
  ↓
Split Out (每个 issue 一个 item)
  ↓
Postgres Execute: 
  INSERT INTO bkd_snapshot (...) VALUES (...) 
  ON CONFLICT (issue_id) DO UPDATE SET ...
```

关键：

- 解析 SSE 用稳定模板（见 [router/router.js parseSse](../router/router.js)）
- upsert 幂等，重试无副作用
- 整个 workflow 失败时**自己报警**（BKD 挂了你得知道，不然看的数据是陈的）

## 读取路径 1：实时兜底（Metabase Alert）

4 条规则，SQL 在 [observability/queries/](../observability/queries/)：

| Alert | 触发 | 起步动作 | 升级动作 |
|---|---|---|---|
| [Layers drift](../observability/queries/alert-layers-drift.sql) | analyze-done issue 没 layer:* tag | Lark | BKD 加 block-fanout tag |
| [Duplicate stage](../observability/queries/alert-duplicate-stage.sql) | 同 REQ 同 stage > 1 个活跃 issue | Lark | cancel 较新的 |
| [Bugfix runaway](../observability/queries/alert-bugfix-runaway.sql) | 自上次 ci:pass 起 bugfix ≥ 3 | Lark | feat 分支加 circuit-breaker |
| [Stuck session](../observability/queries/alert-stuck-session.sql) | working 超过 stage SLA | Lark | BKD cancel + GH incident |

**分阶段启用**：

1. **第 1-2 周**：全部跑被动（只发 Lark），统计误报率
2. **第 3 周**：误报率 < 5% 的规则升级为"半主动"（加 BKD tag，不 cancel）
3. **第 5 周**：观察 tag-based 兜底没误伤，升级到完全主动（cancel / escalate）

## 读取路径 2：离线分析（Metabase 看板）

5 个基础看板：

| 看板 | 数据源 | 回答的问题 |
|---|---|---|
| REQ timeline | view `req_timeline` | 哪阶段慢？哪些 REQ 异常？|
| Agent 质量 | view `agent_quality` | 谁最靠谱？谁一把过率低？|
| REQ 成本 | view `req_cost` | 一个 REQ 多少钱？趋势？|
| 失败模式 | event_log where kind=action.failed | 最常挂的 3 种？|
| 检测命中 | view `recent_violations` | 兜底规则有没有误报？真问题占多少？|

**所有看板按 `config_version` 拆列 / 筛选**，否则改进前后的数据混在一起看不出效果。

## 读取路径 3：可持续改进闭环（关键）

```
   ┌──────────────────────────────────────┐
   │  1. 观察异常                         │
   │     Metabase 发现 "contract-test     │
   │     一把过率 40%"                    │
   └──────────────┬───────────────────────┘
                  ▼
   ┌──────────────────────────────────────┐
   │  2. 根因诊断                         │
   │     按 failed_tests / stderr_tail    │
   │     GROUP BY 找 top 3 失败模式       │
   └──────────────┬───────────────────────┘
                  ▼
   ┌──────────────────────────────────────┐
   │  3. 生成假设                         │
   │     "contract-test-agent 没读懂      │
   │      OpenAPI 的 oneOf schema"        │
   └──────────────┬───────────────────────┘
                  ▼
   ┌──────────────────────────────────────┐
   │  4. 实施改动                         │
   │     改 prompts.md 的 contract-test   │
   │     section，加 oneOf 处理指令       │
   │     → 改 n8n workflow variable       │
   │       ROUTER_VERSION=prompt-v2       │
   │     → INSERT config_version          │
   │     → INSERT improvement_log 假设    │
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

关键是 **`config_version` 和 `improvement_log`**：它们把每次改动和每条指标挂钩，让改进行为本身变得可被度量，才谈得上"可持续"。

## 容量 / 性能估算

| 表 | 日增 | 年增 | 查询 |
|---|---|---|---|
| event_log | ~50 行 × 5 REQ = 250 行 | ~100K | 索引命中 < 10ms |
| bkd_snapshot | 覆盖式，~几千行 | 不增长 | < 5ms |
| config_version | 改一次加一行 | < 100 | < 1ms |
| improvement_log | 每次改动加一行 | < 100 | < 1ms |

**3 年内完全在 Postgres 单实例舒适区**，不需分区、不需归档。10Gi 盘撑 20 年。

## 覆盖的已知缺陷

本观测系统**兜底**（detect + escalate），不**修复**以下架构缺陷。修复需要单独改 Router / n8n workflow 逻辑。

| 缺陷（[architecture.md §已知未解问题](./architecture.md#已知未解问题)）| 兜底规则 | 残留风险 |
|---|---|---|
| layers 双真相源（frontmatter vs tag）| Layers drift | 90% 的 drift 能抓；完整 frontmatter 对比需 git 访问，后续加 |
| 幂等性软闸（dedup 丢、priorStatusId 穿透）| Duplicate stage | 秒级检测，但 agent 已烧 token。dev 期可接受 |
| Action 无事务 / 幂等 key（retry 创重）| Duplicate stage | 同上 |
| 熔断粒度太粗 / v3.1 实际消失 | Bugfix runaway | 完全兜住；粒度后续可按 scenario 细化 |
| 阶段永不回流 / agent 死循环 | Stuck session | 完全兜住 |

**先兜住，再慢慢修**。每个缺陷被观测抓到之后，`improvement_log` 里记一条"修复假设"，跟踪到 verdict=success 才算真解决。

## 落地顺序

| 阶段 | 工作 | 时间 |
|---|---|---|
| P0 | Postgres + schema + n8n 5 个 tap 节点 + BKD sync workflow | 半天 |
| P1 | Metabase 接入 + 5 个看板 + 4 条 Alert（被动模式）| 半天 |
| P2 | 1 个 REQ 端到端验证，看数据到齐 | 半天 |
| P3（2 周后）| 评估 Alert 误报率，升级主动模式 | 持续 |
| P4（持续）| 按观察到的问题改 prompt / Router / 阈值，走 improvement_log 闭环 | 持续 |

**P0 + P1 + P2 = 1.5 天**。之后是运营阶段，靠 Metabase 看板驱动迭代，不再需要新代码。

## 扩展路径

以下**暂不建**，出现对应信号再加：

| 信号 | 加什么 |
|---|---|
| event_log > 1000 万行 / 查询 > 3s | 分区表 or 迁 ClickHouse |
| 跨服务调用链变多（> 3 个服务）| OpenTelemetry trace |
| 非工程师也要看数据 | Metabase 开权限 + 固化看板 |
| 合规审计日志 3 年 | Loki + S3 归档 |
| 检测规则 > 10 条，Metabase 管理不过来 | observer.py 小服务接管 |
| Router 决策 / action 逻辑复杂到 n8n JSON 难维护 | 移 Python 服务（见架构讨论路径）|

## 引用

- [observability/README.md](../observability/README.md) — 部署 + 运维 + 查询速查
- [observability/schema.sql](../observability/schema.sql) — DDL
- [observability/queries/](../observability/queries/) — 4 条 Alert SQL
- [values/postgresql.yaml](../values/postgresql.yaml) / [values/metabase.yaml](../values/metabase.yaml) — helm values
- [architecture.md](./architecture.md) — 权威架构（本观测系统是其第三支柱）
- [workflow-current.md](./workflow-current.md) — 当前实现状态和已知遗留
