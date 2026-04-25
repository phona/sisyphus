# Tasks for REQ-checker-no-feat-branch-fail-loud-1777123726

## Stage: contract / spec

- [x] author specs/checker-empty-source-guard/spec.md MODIFIED delta: dev_cross_check
      + staging_test 改 per-repo no-feat-branch 为 fail-loud；spec_lint 不变
- [x] 列 6 条 scenario（CNFB-S1..S6）覆盖 fail-loud + Guard C 边界 + per-repo 归因
- [x] 显式说明 Guard C 现在只兜底 Makefile-target-missing（feat 已 push 但仓没 lint /
      test target）

## Stage: implementation

- [x] `orchestrator/src/orchestrator/checkers/dev_cross_check.py`：
  - 把 `[skip] $name: no feat branch / not involved; continue` 改成
    `=== FAIL dev_cross_check: $name has no feat/<REQ> branch on origin — refusing to silent-pass === >&2; fail=1; continue`
  - Guard C 条件改为 `[ "$ran" -eq 0 ] && [ "$fail" -eq 0 ]` 且消息改成
    `0 source repos eligible (no make ci-lint target on feat/<REQ>)`
- [x] `orchestrator/src/orchestrator/checkers/staging_test.py`：
  - 同上结构调整：fail-loud + Guard C 条件改严 + 消息更新为
    `0 source repos eligible (no ci-unit-test+ci-integration-test target on feat/<REQ>)`
- [x] 两个 module docstring 加段说明 "feat/<REQ> 缺失语义"，引本 REQ id

## Stage: unit test

- [x] `orchestrator/tests/test_checkers_empty_source_guard.py`：拆开 Guard C 测试
  - 删除原 3-checker 参数化的 `test_cmd_exits_nonzero_when_no_repo_eligible` /
    `test_guard_c_real_git_repo_without_feat_branch`
  - 替换为 `test_cmd_spec_lint_exits_nonzero_with_zero_eligible_when_no_feat_branch` /
    `test_guard_c_spec_lint_real_git_repo_without_feat_branch`（spec_lint only）
  - 保留 `test_guard_c_stderr_contains_refusing_to_silent_pass`（三 checker 共用
    silent-pass 拒绝子串）
- [x] 新增 `orchestrator/tests/test_checkers_no_feat_branch_fail_loud.py`：
  - CNFB-S1/S2：单仓无 feat → fail-loud + per-repo stderr 行
  - CNFB-S3/S4：feat present + 无 Makefile target → Guard C 仍触发
  - CNFB-S5：spec_lint 行为不变（无 fail-loud emission）
  - CNFB-S6：cmd template 字面量断言（fail-loud 行 + 删除 `[skip]` 旧行 + Guard C 条件）
- [x] 真实 git 子进程 fixture `_make_repo_with_feat_branch`（bare origin + feat 推送）
  支撑 Guard C 在 "feat 已 push" 前提下的测试
- [x] 真实 git 子进程 fixture `_make_repo_without_feat_branch`（git init 无 remote）
  支撑 fail-loud / spec_lint Guard C 测试

## Stage: PR

- [x] git push feat/REQ-checker-no-feat-branch-fail-loud-1777123726
- [x] gh pr create
