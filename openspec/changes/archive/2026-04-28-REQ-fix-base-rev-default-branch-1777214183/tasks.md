# Tasks — REQ-fix-base-rev-default-branch-1777214183

## Stage: contract / spec

- [x] 写 `proposal.md`：背景 / 方案 / 影响 / 改的文件
- [x] 写 `specs/dev-cross-check-base-rev/spec.md`（ADDED Requirements + Scenarios）

## Stage: implementation

- [x] `orchestrator/src/orchestrator/checkers/dev_cross_check.py`：在 `_build_cmd`
      里 `make ci-lint` 前插入 `default_branch=$(git symbolic-ref ...)` 解析步骤；
      把静态链改成 `[ -n "$default_branch" ] && git merge-base HEAD
      "origin/$default_branch"` → main → master → develop → dev → 空
- [x] `orchestrator/src/orchestrator/checkers/dev_cross_check.py` header docstring
      记 REQ-fix-base-rev-default-branch-1777214183 + 行为变化
- [x] `orchestrator/src/orchestrator/actions/create_dev_cross_check.py` header
      注释同步
- [x] 更新 prompt 文档：
  - [x] `prompts/analyze.md.j2` —— 流程描述里 `origin/main` 改成 `origin/<default_branch>`
  - [x] `prompts/bugfix.md.j2` —— Step 3.5 push 前自检命令改用 `git symbolic-ref` 取默认分支
  - [x] `prompts/verifier/dev_cross_check_success.md.j2`
  - [x] `prompts/verifier/dev_cross_check_fail.md.j2`
- [x] 更新仓内 docs：
  - [x] `docs/integration-contracts.md` §2 表格 + §2.2 BASE_REV 约定 chain + 解释
  - [x] `docs/architecture.md` dev-cross-check 表格 + 伪代码
  - [x] `docs/state-machine.md` `dev-cross-check-running` 行
  - [x] `docs/cookbook/ttpos-flutter-makefile.md` §5 BASE_REV 约定改成新链 + 说明
        `release` 默认分支不再 fall-through

## Stage: tests

- [x] `orchestrator/tests/test_checkers_dev_cross_check.py`：
  - [x] header docstring 同步新行为
  - [x] `_assert_for_each_repo_cmd` 加 `git symbolic-ref` / `default_branch=` /
        `"origin/$default_branch"` / `origin/master` 断言
  - [x] 新增 `test_build_cmd_resolves_default_branch_from_origin_head_first`
        专项测试，验
        - default_branch 解析步骤存在
        - 由 `[ -n "$default_branch" ]` gate 防空值歧义
        - 静态链顺序固定 main → master → develop → dev
        - 全 miss 退空字符串

## Stage: PR

- [x] `cd orchestrator && uv run pytest tests/test_checkers_dev_cross_check.py
      tests/test_makefile_ci_targets.py tests/test_contract_makefile_ci_targets.py`
- [x] `cd orchestrator && uv run ruff check src/ tests/`
- [x] git push origin feat/REQ-fix-base-rev-default-branch-1777214183
- [x] gh pr create
