# Sisyphus 可观测性 — 部署与运维

> Postgres + Metabase 最小可观测系统。设计原理见 [docs/observability.md](../docs/observability.md)；
> 看板配置 + SQL 详细见 [sisyphus-dashboard.md](./sisyphus-dashboard.md)。
>
> 本文只讲怎么部署、怎么查、怎么加新规则。

## 组件清单

| 组件 | 部署方式 | 职责 |
|---|---|---|
| Postgres 15 | Bitnami helm chart, values: [../values/postgresql.yaml](../values/postgresql.yaml) | 事件 + 快照 + 配置版本 + 改进日志 |
| Metabase | pmint/metabase helm, values: [../values/metabase.yaml](../values/metabase.yaml) | BI 看板 + Alert + 飞书 webhook 通知 |
| orchestrator `store/` 模块 | 内嵌在 orchestrator Pod | 写 event_log / req_state / artifact_checks / stage_runs / verifier_decisions |
| orchestrator `snapshot.py` cron | 内嵌后台 task，每 5 min | BKD list-issues → upsert bkd_snapshot |

## 部署

### 1. Postgres

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install sisyphus-postgresql bitnami/postgresql \
  -n sisyphus --create-namespace \
  -f values/postgresql.yaml
```

orchestrator 启动时跑 [migrations/](../orchestrator/migrations/) 0001~0005，建所有表 + view。手动确认：

```bash
kubectl exec -n sisyphus sisyphus-postgresql-0 -- \
  psql -U sisyphus -d sisyphus -c '\dt'
```

应看到：`req_state` / `event_log` / `bkd_snapshot` / `artifact_checks` / `stage_runs` / `verifier_decisions` / `config_version` / `improvement_log`。

### 2. Metabase

```bash
helm repo add pmint https://pmint93.github.io/helm-charts
helm install sisyphus-bi pmint/metabase \
  -n sisyphus \
  -f values/metabase.yaml
```

启动后 UI → **Admin → Databases → Add database**：

- Engine: PostgreSQL
- Host: `sisyphus-postgresql.sisyphus.svc.cluster.local`
- Database: `sisyphus`
- Username / Password: 见 `values/postgresql.yaml`

### 3. orchestrator 写入

不需要单独配 —— orchestrator 服务起来后，每条 webhook 处理都会自动写表（`store/event_log.py` / `store/req_state.py` / `store/artifact_checks.py` 等）。

### 4. BKD 快照同步

orchestrator 内置后台 task `snapshot.py`，每 5 分钟跑：BKD list-issues → 解析 → upsert `bkd_snapshot`。无需额外部署。

间隔 / on/off 由 config 控（`bkd_snapshot_enabled` / `bkd_snapshot_interval_sec`）。

## Alert（实时兜底）

每条在 Metabase 里存为 Question + Alert：

| Alert | SQL 文件 | 触发频率 | 命中动作 |
|---|---|---|---|
| Duplicate stage | [queries/alert-duplicate-stage.sql](./queries/alert-duplicate-stage.sql) | 每 5 分钟 | 飞书 + （未来）orchestrator emit cancel |
| Stuck session | [queries/alert-stuck-session.sql](./queries/alert-stuck-session.sql) | 每 5 分钟 | 飞书（双保险；watchdog M8 已自动 escalate） |
| Layers drift | [queries/alert-layers-drift.sql](./queries/alert-layers-drift.sql) | （已废）v0.2 不再硬编码 layers，可删 |
| Bugfix runaway | （文件已删）M14c 后由 verifier escalate 接管 | — | — |

起步全部跑**被动模式**（只发飞书，不自动干预）。攒 2 周数据，误报率 < 5% 才升级为主动模式。

## 13 个 Metabase 看板（Q1-Q13）

完整 SQL + Question 配置在 [sisyphus-dashboard.md](./sisyphus-dashboard.md)。SQL 文件在 [queries/sisyphus/](./queries/sisyphus/)。

| Question | 主题 | 数据源 |
|---|---|---|
| Q1-Q5（M7） | 钻牛角尖 / 慢异常 / 通过率 / 失败分桶 / 在飞 REQ | `artifact_checks` |
| Q6-Q7 | 周通过率趋势 / duration P50/P95 | `stage_runs` |
| Q8-Q9 | verifier 准确率 / fixer 修复成功率 | `verifier_decisions` |
| Q10 | token 成本 | `stage_runs` |
| Q11 | dev 并行加速比 | `stage_runs`（M14d 后启用） |
| Q12-Q13 | bugfix loop 异常 / watchdog escalate 频率 | 多表 join |

**所有看板按 `config_version` 切片**，否则改进前后的数据混在一起看不出效果。

## 可持续改进闭环

1. Metabase 看板发现异常（例：verifier pr_ci_fail 一把就 escalate 占 60%）
2. 诊断根因（看 `verifier_decisions.reason` + 关联 `event_log`）
3. 改 prompt / 阈值，**bump config_version**（见下）
4. 在 `improvement_log` 插一行假设 + baseline + target
5. 等 2 周，跑 `metric_sql` 拿 `observed_value`，填 `verdict`

```sql
-- 改动时 bump 版本
INSERT INTO config_version (version_hash, kind, target, git_commit, diff_summary, author)
VALUES ('verifier-pr-ci-v2', 'prompt', 'verifier/pr_ci_fail.md.j2',
        '<git sha>', '加 retry_checker 提示', 'tc@uzaqfood.com');

UPDATE config_version SET retired_at = now()
WHERE kind='prompt' AND target='verifier/pr_ci_fail.md.j2'
  AND version_hash <> 'verifier-pr-ci-v2' AND retired_at IS NULL;

INSERT INTO improvement_log
  (hypothesis, config_version, metric_name, metric_sql, baseline_value, target_value)
VALUES
  ('verifier pr_ci_fail prompt 加 retry_checker 选项，预期 retry 占比 ↑',
   'verifier-pr-ci-v2',
   'retry_checker_share',
   'SELECT 100.0 * SUM(CASE WHEN decision_action=''retry_checker'' THEN 1 ELSE 0 END) / COUNT(*) FROM verifier_decisions WHERE stage=''pr_ci''',
   5, 25);
```

## 查询速查

```sql
-- REQ 全貌（卡哪了）
SELECT req_id, state, updated_at, context->>'verifier_stage' AS verifier_stage
FROM req_state
WHERE req_id = 'REQ-29';

-- 看一个 REQ 的事件历史
SELECT ts, kind, router_action, exit_code
FROM event_log
WHERE req_id = 'REQ-29'
ORDER BY ts;

-- 过去 7 天各 stage P50/P95 耗时
SELECT stage,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_sec) AS p95
FROM stage_runs
WHERE started_at > now() - interval '7 days'
GROUP BY stage ORDER BY p95 DESC;

-- verifier 决策分布
SELECT decision_action, COUNT(*) AS cnt
FROM verifier_decisions
WHERE created_at > now() - interval '7 days'
GROUP BY decision_action
ORDER BY cnt DESC;

-- 某改进前后对比
SELECT config_version,
       ROUND(100.0 * SUM(CASE WHEN final_state = 'pass' THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0), 1) AS pass_rate
FROM stage_runs
WHERE stage = 'staging-test' AND started_at > now() - interval '30 days'
GROUP BY config_version;
```

## 加新检测规则

1. 在 `queries/` 加一个 SQL 文件 `alert-xxx.sql`
2. Metabase 里 **New Question → SQL → 贴 SQL**
3. 保存后 **Add Alert → 条件：row count > 0 → 通道：飞书 webhook**
4. 攒 2 周数据验证误报率
5. 误报率合格后升级为"主动干预"：写 orchestrator action 调 BKD cancel / emit Event

## 容量

| 表 | 日增 | 年增 | 备注 |
|---|---|---|---|
| event_log | ~100 行 × 5 REQ = 500 行 | ~180K | 10Gi 盘撑 20 年 |
| artifact_checks | ~20 行 × 5 REQ = 100 行 | ~36K | — |
| stage_runs | ~10 行 × 5 REQ = 50 行 | ~18K | — |
| verifier_decisions | ~5 行 × 5 REQ = 25 行 | ~9K | — |
| bkd_snapshot | 覆盖式，~几千行 | 不增长 | — |
| config_version / improvement_log | 改一次加一行 | < 100 | — |

## 备份

```bash
# 每日 dump（K3s CronJob）
pg_dump -h sisyphus-postgresql -U sisyphus sisyphus \
  | gzip > /backup/sisyphus_$(date +%F).sql.gz
```

event_log / artifact_checks / stage_runs / verifier_decisions 都是 append-only，可以放心 dump；Metabase 元数据（看板、用户、凭证）在 Metabase 自己的 H2/Postgres 里，单独 dump。
