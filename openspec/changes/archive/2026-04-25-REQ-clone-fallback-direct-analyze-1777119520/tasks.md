# Tasks: REQ-clone-fallback-direct-analyze-1777119520

## Stage: spec

- [x] `openspec/changes/REQ-clone-fallback-direct-analyze-1777119520/proposal.md`
- [x] `openspec/changes/REQ-clone-fallback-direct-analyze-1777119520/tasks.md`
- [x] `openspec/changes/REQ-clone-fallback-direct-analyze-1777119520/specs/multi-layer-involved-repos-fallback/spec.md`
- [x] `openspec/changes/REQ-clone-fallback-direct-analyze-1777119520/specs/multi-layer-involved-repos-fallback/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/actions/_clone.py`：
  - 重构 `_resolve_repos` → public `resolve_repos(ctx, *, tags, default_repos) -> (list, str)` 含 4 层逻辑
  - 加 `_extract_repo_tags(tags)` + `_REPO_SLUG_RE`（`repo:<org>/<name>` 解析 + slug 校验）
  - 加 `_normalize_repos(raw)`：list/tuple → list[str] + 顺序保留去重
  - `clone_involved_repos_into_runner` 加 keyword-only `tags` + `default_repos`，向后兼容
  - log `clone.exec` / `clone.done` / `clone.failed` 都带 `source` 字段（哪一层命中）

- [x] `orchestrator/src/orchestrator/config.py`：
  - 加 `default_involved_repos: list[str] = Field(default_factory=list)`
  - env name: `SISYPHUS_DEFAULT_INVOLVED_REPOS`（含中文注释说明用法）

- [x] `orchestrator/src/orchestrator/actions/start_analyze.py`：
  - 调 `clone_involved_repos_into_runner` 时传 `tags=tags, default_repos=settings.default_involved_repos`
  - 模块 docstring 增一段 REQ-clone-fallback-direct-analyze-1777119520 说明

- [x] `orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py`：
  - 同上传参（intake 路径正常 L1 命中，传参保持调用形状一致 + 兜底）

## Stage: tests

- [x] `orchestrator/tests/test_actions_start_analyze.py`：补 8 个 case
  - `test_clone_helper_uses_repo_tags_when_ctx_empty`
  - `test_clone_helper_uses_default_repos_when_ctx_and_tags_empty`
  - `test_clone_helper_returns_none_when_all_layers_empty`
  - `test_resolve_repos_priority_order`
  - `test_resolve_repos_skips_empty_layers`
  - `test_extract_repo_tags_validates_slug`
  - `test_extract_repo_tags_handles_none_and_non_string`
  - `test_start_analyze_passes_repo_tags_and_default_to_clone`
  - `test_start_analyze_uses_settings_default_when_no_ctx_no_tags`
  - `test_start_analyze_skip_remains_when_all_layers_empty`

- [x] `orchestrator/tests/test_contract_clone_fallback_direct_analyze.py`：新增
  - `test_resolve_repos_layer_priority`
  - `test_default_involved_repos_setting_exists`
  - `test_clone_helper_does_not_parse_intent_title_or_body`
  - `test_start_analyze_actions_pass_tags_and_default_to_clone`
  - `test_repo_tag_extraction_validates_slug`

## Stage: PR

- [x] git push feat/REQ-clone-fallback-direct-analyze-1777119520
- [x] gh pr create
