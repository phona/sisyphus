# tasks: REQ-fix-orch-source-repo-tag-1777824479

## Stage: spec

- [x] add `openspec/changes/REQ-fix-orch-source-repo-tag-1777824479/proposal.md`
- [x] add MODIFIED Requirement to `specs/multi-layer-involved-repos-fallback/spec.md`
  delta — 4-layer 升 5-layer、L0 = `tags.source-repo`
- [x] add ADDED Requirement to same delta — `source-repo:<org>/<name>` slug 校验 +
  与 `repo:` tag 隔离
- [x] add scenarios SRTO-S1 ~ SRTO-S4

## Stage: implementation

- [x] `orchestrator/src/orchestrator/actions/_clone.py` —— 新增
  `_SOURCE_REPO_TAG_PREFIX` 常量、`_extract_source_repo_tags` 函数；
  `_extract_repo_tags` refactor 走共享 `_extract_tags_with_prefix` helper
- [x] `orchestrator/src/orchestrator/actions/_clone.py` —— `resolve_repos`
  在 layer table 顶部插入 L0 = `("tags.source-repo", ...)`
- [x] `orchestrator/src/orchestrator/actions/_clone.py` —— docstring
  更新（5-layer 表 + L0 vs L3 对比）

## Stage: tests

- [x] `orchestrator/tests/test_contract_clone_fallback_direct_analyze.py` ——
  扩 `test_resolve_repos_layer_priority` 覆盖 5 层全 fall-through
- [x] `orchestrator/tests/test_contract_source_repo_tag_override.py` ——
  新文件覆盖 L0 winning scenario + 多 tag 合并 + 非法 slug 拒绝 +
  closes #362 实证场景
- [x] `make ci-lint` 全绿
- [x] `make ci-unit-test` 全绿
- [x] `make ci-integration-test` 全绿（无 PG → exit 5 视 pass）

## Stage: PR

- [x] git push origin feat/REQ-fix-orch-source-repo-tag-1777824479
- [x] gh pr create --label sisyphus，PR body 末尾贴 sisyphus:cross-link footer
