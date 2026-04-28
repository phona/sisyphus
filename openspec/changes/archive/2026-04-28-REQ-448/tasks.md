# Tasks — REQ-448

> owner: analyze-agent
> 所有 checkbox 完成时勾上，反映真实做了的事。

## Stage: spec

- [x] `proposal.md` — 动机 + 方案 + 影响
- [x] `specs/metabase-setup/spec.md` — ADDED Requirements + MBS-S1..S14 scenarios
- [x] `tasks.md`（本文件）

## Stage: implementation

- [x] `observability/setup_metabase.py` — Metabase REST API 客户端 + provision 逻辑 + CLI
  - [x] `MetabaseClient` — login / list_databases / find_database_id / create_database /
        get_or_create_database / list_cards / find_card_id / create_card / update_card /
        get_or_create_card / list_dashboards / find_dashboard_id / create_dashboard /
        add_cards_to_dashboard / get_or_create_dashboard
  - [x] `QUESTIONS` — 18 条 QuestionSpec（number, filename, name, display, cache_ttl, dashboard）
  - [x] `DASHBOARDS` — 3 条 DashboardSpec（m7 / m14e / fixer）含 grid layout
  - [x] `load_sql()` — 从 `observability/queries/sisyphus/` 读 SQL 文件
  - [x] `provision()` — idempotent，支持 force + dry_run
  - [x] CLI — `--url/--user/--pass/--db-host/--db-port/--db-name/--db-user/--db-pass/--force/--dry-run`

## Stage: tests

- [x] `orchestrator/tests/test_setup_metabase.py`
  - [x] MBS-S1: load_sql 对所有 Q1–Q18 SQL 文件返回非空内容
  - [x] MBS-S2: login 存储 session token
  - [x] MBS-S3: get_or_create_card 未找到时创建，找到时跳过
  - [x] MBS-S4: get_or_create_card force=True 时更新已有卡片
  - [x] MBS-S5: get_or_create_dashboard 创建时带 layout cards
  - [x] MBS-S6: get_or_create_dashboard 已存在时跳过（非 force）
  - [x] MBS-S7: provision 返回正确的 created/skipped 计数
  - [x] MBS-S8: dry_run 不发任何 HTTP 请求
  - [x] MBS-S9: QUESTIONS 恰好 18 条，编号 1..18 有序
  - [x] MBS-S10: DASHBOARDS 恰好 3 条，key = {m7, m14e, fixer}
  - [x] MBS-S11: 每条 QUESTION 的 SQL 文件磁盘上存在且非空
  - [x] MBS-S12: cache_ttl 分组与 spec 吻合（30s/120s/1800s）
  - [x] MBS-S13: main() 缺必填参数时返回 1
  - [x] MBS-S14: find_database_id 按 host+dbname 匹配，不匹配返回 None

## Stage: verify

- [x] `make ci-lint` pass（ruff check orchestrator/src/ orchestrator/tests/）
- [x] `make ci-unit-test` pass（1410 tests passed）
- [x] `openspec validate openspec/changes/REQ-448` pass

## Stage: PR

- [x] `git push origin feat/REQ-448`
- [x] `gh pr create --label sisyphus`
