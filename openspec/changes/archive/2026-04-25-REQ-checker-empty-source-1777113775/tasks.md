# Tasks: REQ-checker-empty-source-1777113775

## Stage: spec

- [x] `openspec/changes/REQ-checker-empty-source-1777113775/proposal.md`
- [x] `openspec/changes/REQ-checker-empty-source-1777113775/tasks.md`
- [x] `openspec/changes/REQ-checker-empty-source-1777113775/specs/checker-empty-source-guard/spec.md`
- [x] `openspec/changes/REQ-checker-empty-source-1777113775/specs/checker-empty-source-guard/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/checkers/spec_lint.py::_build_cmd`：
  加 Guard A (`/workspace/source` 目录存在) + Guard B (`find -mindepth 1 -maxdepth 1 -type d` 数子目录) + Guard C (`ran` 计数器，只在仓有 feat 分支 + `openspec/changes/<REQ>/` 时 +1)
- [x] `orchestrator/src/orchestrator/checkers/dev_cross_check.py::_build_cmd`：
  加同样三道 guard（C 的 eligibility = 有 feat 分支 + Makefile `^ci-lint:` target）
- [x] `orchestrator/src/orchestrator/checkers/staging_test.py::_build_cmd`：
  加同样三道 guard（C 的 eligibility = 有 feat 分支 + Makefile `^ci-unit-test:` + `^ci-integration-test:` target）

## Stage: tests

- [x] `orchestrator/tests/test_checkers_spec_lint.py`（新增）：
  - 既有 pass/fail/timeout 等价覆盖
  - `test_build_cmd_emits_workspace_source_existence_guard`
  - `test_build_cmd_emits_repo_count_zero_guard`
  - `test_build_cmd_emits_zero_eligible_guard`
- [x] `orchestrator/tests/test_checkers_dev_cross_check.py`：append 同三个 cmd-shape 单测
- [x] `orchestrator/tests/test_checkers_staging_test.py`：append 同三个 cmd-shape 单测
- [x] `orchestrator/tests/test_checkers_empty_source_guard.py`（新增，端到端）：
  - 用 subprocess 跑 patched cmd（把 `/workspace/source` 替换成 tmp_path）
  - 三个 guard × 三个 checker = 9 个 parametrized case，**真**用 bash exec 验证 exit ≠ 0

## Stage: PR

- [x] git push feat/REQ-checker-empty-source-1777113775
- [x] gh pr create
