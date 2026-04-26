# REQ-observability-metabase-1777189271: feat(obs): land 13 Metabase queries from M7+M14e

## Why

`docs/observability.md` 把 sisyphus 可观测性定为 **Postgres 主库 + Metabase BI 看板**：
事件入 `event_log` / `req_state` / `artifact_checks` / `stage_runs` / `verifier_decisions`，
人工通过 Metabase Question 看板诊断 "checker 钻牛角尖在哪"、"verifier 判决准不准"、
"fixer 修得怎么样" 等问题。M7 milestone 立了 5 条基于 `artifact_checks` 的运维看板
（Q1–Q5），M14e 追加 8 条质量看板（Q6–Q13）打 `stage_runs` / `verifier_decisions`。

各份产出（SQL 文件、迁移、orchestrator 写入逻辑）已陆续 land 进 main：
M7 看板由 PR #9 落 SQL 文件 + dashboard md，M14e 看板由 PR #20 落 SQL + 迁移
0004/0005 + 写入 store 模块。但**这些产出从未走过 sisyphus 的 openspec 全责
交付契约（M17）**——没有 `openspec/changes/REQ-…/`，没有 capability spec
描述这 13 条 question 的契约，未来一改 SQL 列名 / 删一个 question / 把
`stage_runs` schema 微调，没有 spec scenario 兜底，运维看板会沉默退化。

> **本 REQ 的工作性质是 "形式化已交付资产"**：所有 SQL 文件 + 迁移 + dashboard
> md 都已经在 main，工作主体不是写新代码，而是为这 13 条 question 立
> capability spec（`observability-dashboard`），把"哪 13 条、各自打哪张表、
> 列契约怎么定、refresh 频率多少"用 scenario 钉死。后续任何想动这 13 条
> 的改动必须走 openspec delta，spec_lint checker 会拦下漂移。

## What changes

- **NEW** `openspec/changes/REQ-observability-metabase-1777189271/`：
  - `proposal.md`（本文件）
  - `tasks.md`（反映真实交付状态：13 SQL + 2 migration + dashboard md 全
    打勾，剩下纯 spec 工作）
  - `specs/observability-dashboard/spec.md`（delta：`## ADDED Requirements`，
    定义 capability `observability-dashboard`，覆盖三块：
    1. M7 五条 question 的契约（Q1–Q5 各自的文件位置 / 主查询表 / 列契约）
    2. M14e 八条 question 的契约（Q6–Q13 同上）
    3. dashboard md 索引契约（refresh 频率、可视化、阈值文档）

- **零业务代码改动**：所有 SQL 文件已存在于
  `observability/queries/sisyphus/01-stuck-checks.sql` ... `13-watchdog-escalate-frequency.sql`，
  `observability/sisyphus-dashboard.md` 已索引，迁移 `0004_stage_runs.sql` /
  `0005_verifier_decisions.sql` 已 ship。本 REQ 只读，不改这些文件。

- **测试范围**：本 REQ 不引入业务代码，不需要新 unit test。spec 的 scenario
  全部以**文件存在 / 内容关键词 / `grep` 命中数**为断言，运行时由 sisyphus
  spec_lint（`openspec validate --strict` + `check-scenario-refs.sh`）兜底验证。

## Impact

- **Affected specs**: 新增 capability `observability-dashboard`（done_archive
  阶段 `openspec apply` 会从 `openspec/changes/.../specs/observability-dashboard/spec.md`
  迁出到 `openspec/specs/observability-dashboard/spec.md`）。
- **Affected code**: 无。
- **Affected ops**: 无。Metabase 仍按 `observability/sisyphus-dashboard.md`
  描述手工接入（Question 配置 + Dashboard 布局），13 条 SQL 文件路径不变。
- **Risk**: 极低 —— pure-doc/spec REQ，仅在 spec 层增量约束。spec scenario
  断言全部是只读文件检查，spec_lint 跑挂的唯一可能是 13 条 SQL 文件之一被
  误删 / 重命名，那本来就是要被拦的漂移。
- **Future delta**: 本 capability 留出后续追加 Q14–Q18（fixer audit + silent-pass
  detector）的口子 —— 它们各自由独立 REQ 走 MODIFIED Requirements 加进来，
  本 REQ 不抢工作量。
