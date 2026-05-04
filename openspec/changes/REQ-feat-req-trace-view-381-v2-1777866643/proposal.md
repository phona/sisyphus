# Proposal: REQ trace view — sisyphus-trace CLI + Metabase Q24

Refs phona/sisyphus#381 (origin), #380 (verifier resume webhook bug — first user).

## 背景

Debug "REQ 卡住不动" 当前路径（#381 描述）：
1. `curl` BKD 拿 issue tags + sub-issue 列表
2. `grep` `webhook.py` / `state.py` / `actions/` 源码猜 orch 行为
3. 手动在脑子里拼时间线 → 单次 ~30min

实际数据全在 PG 主库 `sisyphus`：

- `req_state.history` JSONB array — state 转移序列（`{ts, from, to, event, action}`）
- `stage_runs` — agent 调用起止 + 模型 + 成本
- `verifier_decisions` — verifier 判决（pass/fix/escalate）
- `artifact_checks` — 机械 checker 通过/失败 + cmd / stderr_tail

缺的是按 ts 排序的统一 timeline 视图：
单 REQ 的 4 表事件按时间轴聚合一行一事件。

> 注：`event_log`（n8n / orch tap）住在独立 DB `sisyphus_obs`，跨库 view 不做（见
> `orchestrator/migrations/0002_observability_views.sql` 注释）。本 REQ 范围只覆盖
> 主库 4 表。CLI 留扩展点，将来想加 event_log 时改一处即可。

## 提议

### A. Metabase Question Q24（SQL，主库内 UNION）

`observability/queries/sisyphus/24-req-trace.sql`：UNION 4 个子查询，按 `ts ASC`
排序，参数 `{{req_id}}`。返回列：

| 列 | 含义 |
|---|---|
| `ts` | 事件时间戳（TIMESTAMPTZ） |
| `kind` | `trans` / `stage` / `verify` / `check` 之一 |
| `detail` | 单行可读字符串（如 `INIT → ANALYZING (event=intent.analyze action=create_analyze)`） |

> Q23 已被 `23-first-turn-token-composition.sql` 占用（REQ-feat-agent-turns-collector-1777796671），
> 本次按 Metabase 看板既有的 Q21/Q22 双号惯例顺势改用 **Q24**。intent issue
> 沟通时写的是 Q23，PR description 把改号原因记下来。

### B. CLI `sisyphus-trace <REQ-id>`

`scripts/sisyphus-trace.py`：

- 默认走 kubectl exec 到 `sisyphus-postgresql-0` pod 跑 `psql -d sisyphus`
  （和 `sisyphus-admin.py` 完全同款 helper，避免重复一份外部 PG 客户端）
- `--base-url` / `--dsn` 留口子，未来若 PG 暴露外网端口可绕 kubectl
- `--json` 输出原始 JSON 行，方便 pipe `jq` / Metabase 参数化复现
- 默认输出 ASCII 时间线：

```
sisyphus-trace REQ-feat-req-trace-view-381-v2-1777866643
─────────────────────────────────────────────────────────
02:25:46 [trans]   INIT → ANALYZING (event=intent.analyze action=create_analyze)
02:25:50 [stage]   analyze start (run_id=2231 model=claude-opus-4-7)
02:33:26 [stage]   analyze end pass (token_in=183k token_out=42k)
02:33:27 [trans]   ANALYZING → SPEC_LINT_RUNNING
02:33:30 [check]   spec_lint passed cmd="openspec validate ..." dur=3.1s
03:00:00 [verify]  pr_ci → escalate (conf=high reason="ci red, no fix path")
03:00:01 [trans]   PR_CI_RUNNING → ESCALATED
```

### C. 文档

- `observability/sisyphus-dashboard.md` 新增 Q24 章节 + 看板布局放 "终态记账"
  下面（debug 类常和事后分析配着看）
- `CLAUDE.md` "开发规范" 段后增加 "REQ 卡住怎么 debug" 小段，引到这个工具

## 不在范围内

- `event_log`（sisyphus_obs DB）合并：留 follow-up REQ
- replay-from-trace（#377）：那是回放 payload，本 REQ 是观察现有
- 任何状态机改动 / 写新表 / 新 migration —— 全部复用现有数据

## 影响

| 依赖 | 验证方式 |
|---|---|
| 现有 22 条 Metabase Q | 新增 Q24，不动 Q1–Q23，dashboard.md 加章节不删旧 |
| `sisyphus-admin.py` PG helper | 重用其 `_pg_query` 风格（kubectl exec + psql -t -A），不引新依赖 |
| `req_state.history` JSONB schema | 只读 `{ts, from, to, event, action}` 5 个 key，schema 变了仍 graceful（缺 key 显示 `?`） |
| `stage_runs` / `verifier_decisions` / `artifact_checks` | 只读，不写 |
