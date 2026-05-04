# Tasks: REQ trace view (sisyphus-trace + Q24)

## Stage: spec
- [x] 写 proposal.md（背景 / 范围 / 不在范围 / 影响）
- [x] 写 tasks.md
- [x] 写 specs/req-trace-cli/spec.md（ADDED Requirements + Scenarios）

## Stage: implementation
- [x] `observability/queries/sisyphus/24-req-trace.sql` —— UNION 4 子查询 + 参数化 + 注释
- [x] `scripts/sisyphus-trace.py` —— argparse + `_pg_query` helper + ASCII renderer + `--json`
- [x] `observability/sisyphus-dashboard.md` —— 加 Q24 章节
- [x] `CLAUDE.md` —— 加 "REQ 卡住怎么 debug" 段引 `sisyphus-trace`

## Stage: tests
- [x] `orchestrator/tests/test_scripts_sisyphus_trace.py` —— 渲染 / 参数解析 / 退码（importlib 加载顶层 scripts/sisyphus-trace.py）
- [x] 跑 `make ci-lint` 全绿
- [x] 跑 `make ci-unit-test` 全绿
- [x] 跑 `make ci-integration-test`（无 PG → exit 5 → pass）

## Stage: PR
- [x] git push origin feat/REQ-feat-req-trace-view-381-v2-1777866643
- [x] gh pr create --label sisyphus + cross-link footer
