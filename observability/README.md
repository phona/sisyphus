# Sisyphus 可观测性

Postgres + Metabase + n8n tap 的最小可观测系统。设计原理见 [docs/observability.md](../docs/observability.md)；本文只讲怎么部署、怎么查、怎么加新规则。

## 组件清单

| 组件 | 部署方式 | 职责 |
|---|---|---|
| Postgres 15 | Bitnami helm chart，values: [../values/postgresql.yaml](../values/postgresql.yaml) | 事件 + 快照 + 配置版本 + 改进日志 |
| Metabase | pmint/metabase helm，values: [../values/metabase.yaml](../values/metabase.yaml) | BI 看板 + Alert + Lark webhook 通知 |
| n8n tap 节点 | 现有 n8n workflow 里加 Postgres Insert 节点 | 把事件推到 event_log |
| n8n BKD sync | 新增一个 Schedule workflow，每 5 分钟 list-issues → upsert bkd_snapshot | 把 BKD 当前状态镜像到 Postgres |

## 部署

### 1. Postgres

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install sisyphus-pg bitnami/postgresql \
  -n sisyphus --create-namespace \
  -f values/postgresql.yaml
```

chart 的 `initdb.scripts` 已经把 [schema.sql](./schema.sql) 挂进去，启动时自动建表。确认：

```bash
kubectl exec -n sisyphus sisyphus-pg-postgresql-0 -- \
  psql -U sisyphus -d sisyphus_obs -c '\dt'
```

应该看到 4 张表 + 4 个 view。

### 2. Metabase

```bash
helm repo add pmint https://pmint93.github.io/helm-charts
helm install sisyphus-bi pmint/metabase \
  -n sisyphus \
  -f values/metabase.yaml
```

启动后访问 Metabase UI → **Admin → Databases → Add database**：

- Engine: PostgreSQL
- Host: `sisyphus-pg-postgresql.sisyphus.svc.cluster.local`
- Database name: `sisyphus_obs`
- Username / Password: 见 `values/postgresql.yaml`

### 3. n8n tap 节点

在现有 `charts/n8n-workflows/v3.1/v3-events.json` 里，**5 个位置**各加一个 Postgres Insert 节点（不改现有节点，并联挂出去）：

| tap | 位置 | kind |
|---|---|---|
| tap1 | `[ENTRY] Ctx 提取` 后 | `webhook.received` |
| tap2 | `Router` 节点后 | `router.decision` |
| tap3 | 每个 action HTTP 节点的成功输出后 | `action.executed` |
| tap4 | 每个 action HTTP 节点的 error 输出 | `action.failed` |
| tap5 | `[SPG] Gate check` 后 | `gate.check` |

tap 节点配置模板见 [docs/observability.md#tap-节点模板](../docs/observability.md#tap-节点模板)。

**关键**：tap 节点必须 `timeout=2s` + `onError=continueRegularOutput`。**观测不能阻塞业务**。

### 4. BKD sync workflow

新建一个 n8n workflow：Schedule (每 5 min) → BKD list-issues → Code 节点 parse SSE + flatten → Postgres Upsert。

样板在 [docs/observability.md#bkd-sync-workflow](../docs/observability.md#bkd-sync-workflow)。

## 4 条 Metabase Alert（实时兜底）

每条在 Metabase 里存为 Question + Alert：

| Alert | SQL 文件 | 触发频率 | 命中动作 |
|---|---|---|---|
| Layers drift | [queries/alert-layers-drift.sql](./queries/alert-layers-drift.sql) | 每 5 分钟 | Lark webhook |
| Duplicate stage | [queries/alert-duplicate-stage.sql](./queries/alert-duplicate-stage.sql) | 每 5 分钟 | Lark + BKD cancel 较新的 issue |
| Bugfix runaway | [queries/alert-bugfix-runaway.sql](./queries/alert-bugfix-runaway.sql) | 每 10 分钟 | Lark + BKD 加 circuit-breaker tag |
| Stuck session | [queries/alert-stuck-session.sql](./queries/alert-stuck-session.sql) | 每 5 分钟 | Lark + BKD cancel-issue |

起步全部跑**被动模式**（只发 Lark，不自动干预）。攒 2 周数据，误报率 < 5% 才升级为主动模式。

## 5 个 Metabase 看板

| 看板 | 来源 |
|---|---|
| REQ timeline | view: `req_timeline` |
| Agent 质量 | view: `agent_quality` |
| REQ 成本 | view: `req_cost` |
| 失败模式分布 | ad-hoc SQL on `event_log` |
| 检测规则命中 | view: `recent_violations` |

**所有看板按 `config_version` 拆列/筛选**，否则改进前后的数据混一起看不出效果。

## 可持续改进闭环

1. Metabase 看板发现异常（例：某 agent 一把过率 < 50%）
2. 诊断根因（`SELECT failed_tests FROM event_log WHERE stage='X' GROUP BY ...`）
3. 改 prompt / Router 规则，**bump config_version**（见下）
4. 在 `improvement_log` 插一行假设 + baseline + target
5. 等 2 周，跑 `metric_sql` 拿 `observed_value`，填 `verdict`

```sql
-- 改动时 bump 版本
INSERT INTO config_version (version_hash, kind, target, git_commit, diff_summary, author)
VALUES ('prompt-ctrtest-v2', 'prompt', 'contract-test-agent',
        '<git sha>', '加 oneOf schema 处理指令', 'weifashi@jbcnet.co.jp');

-- 把当前版本的旧行标记过期
UPDATE config_version SET retired_at = now()
WHERE kind='prompt' AND target='contract-test-agent'
  AND version_hash <> 'prompt-ctrtest-v2' AND retired_at IS NULL;

-- 记录改进假设
INSERT INTO improvement_log
  (hypothesis, config_version, metric_name, metric_sql, baseline_value, target_value)
VALUES
  ('改 contract-test prompt 加 oneOf 处理指令，预期一把过率 ↑',
   'prompt-ctrtest-v2',
   'contract_test_first_pass_rate',
   'SELECT first_pass_rate_pct FROM agent_quality WHERE agent_role=''contract-spec''',
   40, 65);
```

## 查询速查

```sql
-- REQ 全貌
SELECT * FROM req_timeline WHERE req_id='REQ-669' ORDER BY created_at;

-- 过去 7 天各 stage P50/P95 耗时
SELECT stage,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_sec) AS p95
FROM req_timeline
WHERE last_update > now() - interval '7 days'
GROUP BY stage ORDER BY p95 DESC;

-- Router 的决策分布
SELECT router_action, COUNT(*) AS cnt
FROM event_log WHERE kind='router.decision' AND ts > now() - interval '7 days'
GROUP BY router_action ORDER BY cnt DESC;

-- 某改进前后对比
SELECT config_version,
       ROUND(100.0 * COUNT(*) FILTER (WHERE round=0 AND status='done')
             / NULLIF(COUNT(*), 0), 1) AS first_pass_rate
FROM event_log
WHERE stage = 'contract-spec' AND ts > now() - interval '30 days'
GROUP BY config_version;
```

## 加新检测规则的流程

1. 在 `queries/` 加一个 SQL 文件 `alert-xxx.sql`
2. 在 Metabase 里 **New Question → SQL → 贴 SQL**
3. 保存后 **Add Alert → 条件：row count > 0 → 通道：Lark webhook**
4. 攒 2 周数据验证误报率
5. 误报率合格后，把 Alert 升级成"主动干预"：
   - Lark webhook → n8n webhook endpoint
   - n8n 里写一个 action workflow 调 BKD/GH

## 容量

| 表 | 日增 | 年增 | 备注 |
|---|---|---|---|
| event_log | ~50 行 × 5 REQ = 250 行 | ~100K | 10Gi 盘撑 20 年 |
| bkd_snapshot | 覆盖式，~几千行 | 不增长 | — |
| config_version | 改一次加一行 | < 100 | — |
| improvement_log | 每次改动加一行 | < 100 | — |

## 备份

```bash
# 每日 dump（k3s CronJob）
pg_dump -h sisyphus-pg-postgresql -U sisyphus sisyphus_obs \
  | gzip > /backup/sisyphus_obs_$(date +%F).sql.gz
```

event_log 是 append-only 可以放心 dump；Metabase 元数据（看板、用户、凭证）在 Metabase 自己的数据库里，单独 dump。
