# Tasks — REQ-fix-runner-self-heal-394-1777869659

owner: analyze-agent

## Stage: contract / spec

- [x] `openspec/changes/REQ-fix-runner-self-heal-394-1777869659/specs/runner-pod-self-heal/spec.md` —
      ADDED Requirement: orchestrator MUST self-heal missing runner pod via lazy recreate (PVC reused)
- [x] proposal.md / tasks.md / design.md（design.md 不需要 —— 改动很小，proposal 已交代清楚）

## Stage: implementation

- [x] 新增 `orchestrator/src/orchestrator/actions/_runner.py`：`ensure_runner_alive(req_id)` helper
- [x] `checkers/spec_lint.py` 在 `exec_in_runner` 前调 ensure_runner_alive
- [x] `checkers/dev_cross_check.py` 同上
- [x] `checkers/staging_test.py` 同上（baseline + PR 两阶段共用一次 ensure）
- [x] `checkers/analyze_artifact_check.py` 同上
- [x] `actions/create_pr_ci_watch._discover_repos_from_runner` 同上
- [x] `actions/teardown_accept_env._run_single_layer_teardown` / `_run_multi_layer_teardown` 同上

## Stage: tests

- [x] `orchestrator/tests/test_actions_runner_self_heal.py` 覆盖 RSH-S1..S4
- [x] `make ci-lint` 全绿
- [x] `make ci-unit-test` 全绿
- [x] `make ci-integration-test` 全绿（无 PG 自动跳过）

## Stage: PR

- [x] git push feat/REQ-fix-runner-self-heal-394-1777869659
- [x] `gh pr create --label sisyphus`，body 含 sisyphus:cross-link footer
