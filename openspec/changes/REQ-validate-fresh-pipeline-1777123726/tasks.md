# Tasks: REQ-validate-fresh-pipeline-1777123726

## Stage: spec

- [x] `openspec/changes/REQ-validate-fresh-pipeline-1777123726/proposal.md`
- [x] `openspec/changes/REQ-validate-fresh-pipeline-1777123726/tasks.md`
- [x] `openspec/changes/REQ-validate-fresh-pipeline-1777123726/specs/pipeline-marker/spec.md`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/_pipeline_marker.py`：定义模块级常量
  `PIPELINE_VALIDATION_REQ = "REQ-validate-fresh-pipeline-1777123726"`，无副作用，
  不被生产模块引用

## Stage: tests

- [x] `orchestrator/tests/test_contract_pipeline_marker.py`：黑/白盒混合 contract test
  - `test_pvr_s1_module_imports_cleanly` —— `importlib.import_module` 不抛
  - `test_pvr_s2_constant_is_str_and_matches_req_pattern` —— 值匹配
    `^REQ-validate-fresh-pipeline-\d+$`
  - `test_pvr_s3_module_has_no_side_effects_on_reimport` —— 二次 import 同对象
  - `test_pvr_s4_constant_is_not_re_exported_from_package` —— 仓 root 没
    `__init__.py`，常量不出现在 `orchestrator` 命名空间

## Stage: PR

- [x] git push feat/REQ-validate-fresh-pipeline-1777123726
- [x] gh pr create
