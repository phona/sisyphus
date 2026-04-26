# Tasks: REQ-observability-metabase-1777189271

Single-repo REQ (`phona/sisyphus` only). Solo execution (no fan-out)。

> **形式化已交付资产**：13 SQL + 2 migration + dashboard md 已在 main，
> 本 REQ 主体只补 openspec change folder + capability spec。

## Stage: spec

- [x] 写 `openspec/changes/REQ-observability-metabase-1777189271/proposal.md`
- [x] 写 `openspec/changes/REQ-observability-metabase-1777189271/tasks.md`（本文件）
- [x] 写 `openspec/changes/REQ-observability-metabase-1777189271/specs/observability-dashboard/spec.md`（delta：`## ADDED Requirements`，三个 Requirement 覆盖 M7 + M14e + dashboard md 索引契约）
- [x] `openspec validate openspec/changes/REQ-observability-metabase-1777189271 --strict` 通过
- [x] `check-scenario-refs.sh --specs-search-path /workspace/source .` 干净（scenario ID 都是文件检查型，无外部引用）

## Stage: implementation（已 land，本 REQ 只读 / 不改）

- [x] M7 五条 SQL 文件（已 land 于 PR #9）：
  - `observability/queries/sisyphus/01-stuck-checks.sql`
  - `observability/queries/sisyphus/02-check-duration-anomaly.sql`
  - `observability/queries/sisyphus/03-stage-success-rate.sql`
  - `observability/queries/sisyphus/04-fail-kind-distribution.sql`
  - `observability/queries/sisyphus/05-active-req-overview.sql`

- [x] M14e 八条 SQL 文件（已 land 于 PR #20）：
  - `observability/queries/sisyphus/06-stage-success-rate-by-week.sql`
  - `observability/queries/sisyphus/07-stage-duration-percentiles.sql`
  - `observability/queries/sisyphus/08-verifier-decision-accuracy.sql`
  - `observability/queries/sisyphus/09-fix-success-rate-by-fixer.sql`
  - `observability/queries/sisyphus/10-token-cost-by-req.sql`
  - `observability/queries/sisyphus/11-parallel-dev-speedup.sql`
  - `observability/queries/sisyphus/12-bugfix-loop-anomaly.sql`
  - `observability/queries/sisyphus/13-watchdog-escalate-frequency.sql`

- [x] M14e 迁移（已 land 于 PR #20）：
  - `orchestrator/migrations/0004_stage_runs.sql`（+ rollback）
  - `orchestrator/migrations/0005_verifier_decisions.sql`（+ rollback）

- [x] Dashboard 索引文档：`observability/sisyphus-dashboard.md`（每条 question
  对应 SQL 文件链接、可视化形式、人工介入阈值、刷新频率）

## Stage: verify

- [x] 13 条 SQL 文件全部存在于 `observability/queries/sisyphus/`，编号 `01..13` 连续无缺
- [x] `observability/sisyphus-dashboard.md` 引用每条 SQL 文件（`grep -c queries/sisyphus/0?-` ≥ 13 命中）
- [x] M7 五条 SQL 主查询表是 `artifact_checks`（Q5 因联 `req_state` 取 `state`，例外允许）
- [x] M14e 八条 SQL 至少触及 `stage_runs` / `verifier_decisions` 之一
- [x] `make ci-lint BASE_REV=$(git merge-base HEAD origin/main)` —— 仅 docs/spec 改，应短路通过
- [x] `make ci-unit-test` —— 无业务代码改动，无新 unit test，照旧 pass
- [x] `make ci-integration-test` —— 同上

## Stage: PR

- [x] 提 commit 到 `feat/REQ-observability-metabase-1777189271`
- [x] `git push origin feat/REQ-observability-metabase-1777189271`
- [x] `gh pr create`（标题 `feat(obs): land 13 Metabase queries from M7+M14e (REQ-observability-metabase-1777189271)`）
