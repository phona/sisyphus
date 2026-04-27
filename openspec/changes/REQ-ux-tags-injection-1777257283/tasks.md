# Tasks: REQ-ux-tags-injection-1777257283

## Stage: contract / spec

- [x] `openspec/changes/REQ-ux-tags-injection-1777257283/proposal.md`
- [x] `openspec/changes/REQ-ux-tags-injection-1777257283/specs/intent-tag-propagation/contract.spec.yaml`
- [x] `openspec/changes/REQ-ux-tags-injection-1777257283/specs/intent-tag-propagation/spec.md`
- [x] `openspec/changes/REQ-ux-tags-injection-1777257283/tasks.md`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/intent_tags.py` 新模块：
  - 常量 `SISYPHUS_MANAGED_EXACT` / `SISYPHUS_MANAGED_PREFIXES`
  - `is_sisyphus_managed_tag(tag) -> bool`
  - `filter_propagatable_intent_tags(tags) -> list[str]`（保留顺序、去重、跳非字符串）
- [x] `orchestrator/src/orchestrator/actions/start_intake.py`：PATCH tags 合并 `filter_propagatable_intent_tags(tags)`
- [x] `orchestrator/src/orchestrator/actions/start_analyze.py`：PATCH tags 合并 `filter_propagatable_intent_tags(tags)`
- [x] `orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py`：create tags 合并 `filter_propagatable_intent_tags(tags)`
- [x] `orchestrator/src/orchestrator/actions/start_challenger.py`：create tags 合并 `filter_propagatable_intent_tags(tags)`

## Stage: docs

- [x] `docs/api-tag-management-spec.md` 新增 §10 "Hint tags（用户上下文，sisyphus 转发不解释）"

## Stage: tests

- [x] `orchestrator/tests/test_intent_tags.py` 新文件：
  - `is_sisyphus_managed_tag` exact / 前缀 / REQ-id pattern 各一例
  - `filter_propagatable_intent_tags`：保留 hint、过滤 sisyphus-managed、保留顺序、去重、跳非字符串、空输入
- [x] `orchestrator/tests/test_actions_start_analyze.py`：
  - `test_start_analyze_forwards_user_hint_tags` —— tags 含 `repo:foo/bar` + `ux:fast-track` 时 PATCH 的 tags 含两者
  - `test_start_analyze_strips_sisyphus_managed_tags` —— intent issue 上 stale `intent:analyze` / `result:pass` / `pr:owner/repo#1` 不被转发
  - `test_start_analyze_with_finalized_intent_forwards_hint_tags` —— intake 路径创新 issue 时同样转发
- [x] `orchestrator/tests/test_actions_smoke.py`（或新文件）补 `start_intake` / `start_challenger` 转发用例

## Stage: PR

- [x] git push feat/REQ-ux-tags-injection-1777257283
- [x] gh pr create --label sisyphus
