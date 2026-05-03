# Tasks: REQ-impl-orch-cross-repo-env-1777808389

## Stage: contract / spec

- [x] author `specs/cross-repo-env-orchestrator-impl/spec.md` (R1/R2/R3/R6 module API + R4/R8/R10 action behavior)

## Stage: implementation

- [x] new `orchestrator/src/orchestrator/cross_repo_env.py` with `Manifest` dataclass, `parse_manifest`, `resolve_topology`, `workspace_dir_map`, `resolve_branch`
- [x] new migration `orchestrator/migrations/0016_stage_runs_context.sql` (+ rollback): add `context JSONB` column
- [x] extend `orchestrator/src/orchestrator/store/stage_runs.py`: accept context in insert/update + close helper
- [x] refactor `orchestrator/src/orchestrator/actions/create_accept.py`:
  - read source manifest from runner pod
  - branch into single-layer (R8) vs multi-layer (R1–R6, R10) path
  - sequential per-layer `make accept-env-up` with env injection
  - merge endpoint bundle, fail on missing emit fields, record per-layer attribution
- [x] refactor `orchestrator/src/orchestrator/actions/teardown_accept_env.py`:
  - read `accept_layers` from req_state.context
  - reverse-order best-effort `make accept-env-down`
- [x] unit tests `orchestrator/tests/test_cross_repo_env.py` (R1/R2/R3/R6)
- [x] unit tests `orchestrator/tests/test_actions_create_accept_multi_layer.py` (R4/R8/R10)
- [x] author `design.md` capturing schema decisions and trade-offs

## Stage: PR (推之前必须全绿)

- [x] `make ci-lint` 全绿
- [x] `make ci-unit-test` 全绿
- [x] `make ci-integration-test` 全绿（无 PG 自动跳过）
- [x] feat 分支 push origin
- [x] gh pr create with sisyphus label + cross-link footer
