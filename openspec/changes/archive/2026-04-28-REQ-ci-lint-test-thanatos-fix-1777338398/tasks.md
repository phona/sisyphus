## Stage: implementation

- [x] root `Makefile`：`ci-integration-test` 去掉 thanatos 段（主要修复，解锁 PR #170 #172 #179）
- [x] `thanatos/pyproject.toml`：新增 `[dependency-groups].dev` 块（修复 `ci-unit-test` 同类隐患）
- [x] `thanatos/uv.lock`：`uv lock` 重新生成（含 `[package.dev-dependencies]`）
- [x] `.github/workflows/thanatos-ci.yml`：新建 thanatos CI workflow（让 thanatos PR 有 GHA check-runs）

## Stage: spec

- [x] `openspec/changes/REQ-ci-lint-test-thanatos-fix-1777338398/proposal.md`
- [x] `openspec/changes/REQ-ci-lint-test-thanatos-fix-1777338398/specs/thanatos-ci/spec.md`

## Stage: PR

- [x] `git push feat/REQ-ci-lint-test-thanatos-fix-1777338398`
- [x] `gh pr create --label sisyphus`
