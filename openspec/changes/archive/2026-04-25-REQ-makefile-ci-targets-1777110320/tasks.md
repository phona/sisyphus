# Tasks: REQ-makefile-ci-targets-1777110320

## Stage: spec

- [x] `openspec/changes/REQ-makefile-ci-targets-1777110320/proposal.md`
- [x] `openspec/changes/REQ-makefile-ci-targets-1777110320/tasks.md`
- [x] `openspec/changes/REQ-makefile-ci-targets-1777110320/specs/makefile-ci-targets/contract.spec.yaml`
- [x] `openspec/changes/REQ-makefile-ci-targets-1777110320/specs/makefile-ci-targets/spec.md`

## Stage: implementation

- [x] `Makefile`（顶层）：
  - 新增 `ci-lint` target —— 支持 `BASE_REV` env scoping 到变更 `*.py` 文件；空 BASE_REV 或无变更
    文件时退化为全量 `cd orchestrator && uv run ruff check src/ tests/`
  - 新增 `ci-unit-test` target —— `cd orchestrator && uv run pytest -m "not integration"`
  - 新增 `ci-integration-test` target —— `cd orchestrator && uv run pytest -m integration`，退码
    5（no tests collected）视为 pass
  - 删 `dev-cross-check` / `ci-test`（被 ci-lint / ci-unit-test 取代，零外部 callers）
  - `.PHONY` 列表对齐
- [x] `orchestrator/pyproject.toml`：`[tool.pytest.ini_options]` 加 `markers = ["integration: integration tests (selected by ci-integration-test)"]`

## Stage: tests

- [x] `orchestrator/tests/test_makefile_ci_targets.py`：
  - `test_ci_lint_full_scan_passes` —— 调用 `make -n ci-lint`，命令展开含 `ruff check`
  - `test_ci_lint_with_base_rev_scopes_to_changed_files` —— 设 `BASE_REV=HEAD`（无 diff）走 fallback 全量
  - `test_ci_unit_test_excludes_integration_marker` —— `make -n ci-unit-test` 含 `pytest -m "not integration"`
  - `test_ci_integration_test_treats_exit_5_as_pass` —— `make ci-integration-test` 当前 0 个 integration test，预期退码 0
  - `test_integration_marker_is_registered` —— 读 pyproject.toml 验 marker 定义存在
  - `test_dev_cross_check_target_removed` / `test_ci_test_target_removed` —— `make -n dev-cross-check` 失败

## Stage: docs

- [x] `README.md`：更新"接入新业务 repo"段；加一段说明 sisyphus 自己也是 source repo（self-dogfood）

## Stage: PR

- [x] git push feat/REQ-makefile-ci-targets-1777110320
- [x] gh pr create
