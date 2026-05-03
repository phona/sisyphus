# REQ-smoke-pipeline-v3-1777806694: smoke v3: orch full pipeline (global base=main)

## 问题

需要在**零业务面积**下端到端 smoke 一次 sisyphus 自身 orch 管道（intent → analyze → spec_lint
→ challenger → dev_cross_check → staging_test → pr_ci_watch → accept → archive），
确认 global default base = `main` 与近期所有 P3 fix 叠加后整条流水线仍闭合。
任何被改/被加的产物都不能影响 orchestrator 运行时行为。

## 方案

在 `orchestrator/src/orchestrator/_pipeline_marker.py` 追加一个新常量
`SMOKE_PIPELINE_V3_REQ`，其值为本 REQ id 字符串
`"REQ-smoke-pipeline-v3-1777806694"`。

- 与原有 `PIPELINE_VALIDATION_REQ` / `PIPELINE_VALIDATION_REQ_V3` 共存，不破坏现有 contract test
- 不在生产路径被 import —— 只由 contract 测试白盒引用
- 没有 docstring 之外的副作用 —— 模块加载仅定义一个 str 常量，无 IO / 无网络
- 配套 `orchestrator/tests/test_contract_smoke_pipeline_v3.py` 覆盖 SPV3-S1..SPV3-S4

## 取舍

- **为什么追加而不是覆盖** —— 覆盖原常量会导致 PVR-S2 / PVR3-S2 的 pattern 测试失败；
  追加新常量、新测试可让三套 contract 独立共存
- **为什么命名 SMOKE_PIPELINE_V3_REQ** —— REQ id 命名约定从 `validate-fresh-*` / `validate-fresh-3-*`
  迁移到了 `smoke-pipeline-v3-*`；新常量名与 REQ 命名对齐，避免和 `PIPELINE_VALIDATION_REQ_V3`
  字面相撞被人误读
- **为什么不新建模块** —— 单一 `_pipeline_marker.py` 承载所有 fresh-validation marker
  常量，维护成本更低；新 capability `smoke-pipeline-v3` 与已归档的 `pipeline-marker` /
  `pipeline-marker-v3` 平行
