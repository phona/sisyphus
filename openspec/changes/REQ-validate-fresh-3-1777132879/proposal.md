# REQ-validate-fresh-3-1777132879: test(self): full-chain dogfood validation on sha-ddf4ea4 (sonnet default + all P3 fix)

## 问题

sha-ddf4ea4（PR #99：`feat(config): default agent_model = claude-sonnet-4-6`）合并后，sisyphus
默认 agent 模型切换到 claude-sonnet-4-6，同时随附了多条 P3 级别 bug fix（checker fail-loud
当无 feat 分支 / runner_gc RBAC 日志降级 / Metabase silent-pass 检测器等）。这些改动叠加后，
需要在**零业务面积**下跑一次端到端 fresh-pipeline smoke，隔离判断 sisyphus 自身管道是否
仍然正常闭合。

## 方案

在 `orchestrator/src/orchestrator/_pipeline_marker.py` 追加一个新常量
`PIPELINE_VALIDATION_REQ_V3`，其值为本 REQ id 字符串
`"REQ-validate-fresh-3-1777132879"`。

- 与原有 `PIPELINE_VALIDATION_REQ` 常量共存，不破坏现有 contract test
- 不在生产路径被 import —— 只由 contract 测试白盒引用
- 没有 docstring 之外的副作用 —— 模块加载仅定义一个 str 常量，无 IO / 无网络
- 配套 `orchestrator/tests/test_contract_pipeline_marker_v3.py` 覆盖 PVR3-S1..PVR3-S4

## 取舍

- **为什么追加而不是覆盖** —— 覆盖原常量会导致 PVR-S2 的 pattern 测试
  (`^REQ-validate-fresh-pipeline-\d+$`) 失败；追加新常量、新测试可保持两套 contract 独立
- **为什么命名 V3** —— `REQ-validate-fresh-3-*` 的命名约定引入了版本号而非 `-pipeline-`
  后缀；新常量名与 REQ 命名对齐
- **为什么不新建模块** —— 单一 `_pipeline_marker.py` 承载所有 fresh-validation marker
  常量，维护成本更低，且都归属同一 openspec capability（pipeline-marker）
