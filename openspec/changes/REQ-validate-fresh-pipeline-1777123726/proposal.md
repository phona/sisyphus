# REQ-validate-fresh-pipeline-1777123726: test(self): tiny no-op REQ to validate full sisyphus pipeline closes

## 问题

最近一连串自 dogfood REQ（`REQ-makefile-ci-targets-1777110320` / `REQ-self-accept-stage-1777121797` 等）
落地后，sisyphus 的状态机已经具备从 `analyze → spec_lint → dev_cross_check → staging_test →
pr_ci_watch → accept → archive → DONE` 走通**自己仓**的端到端能力。但每一条变更都带"实
际功能"，多块改动叠在一起一旦中间 stage 红，很难判断红的是 **stage 机械层**还是
**业务变更本身**。

我们需要一条**确定性 no-op REQ**做脚手架冒烟（pipeline-validation smoke）：把 stage 与
业务 payload 解耦——若它在 fresh pipeline 上跑 escalate / fail，几乎可以肯定问题
出在 sisyphus 自己（router / checker / accept stack / archive），而不是业务代码。

## 根因

历史 self-dogfood REQ 同时携带行为变更（修 Makefile target、改 accept compose、改
checker 等），所以"管道整体能不能闭合到 DONE"这件事**从未在零业务面积下被独立验证过**。
没有这种校准，每次 pipeline 红都是双因素归因（stage 的锅 vs 变更的锅）。

## 方案

加一个**纯标识**模块作为本 REQ 的产物：

### `orchestrator/src/orchestrator/_pipeline_marker.py`

仅暴露一个模块级常量 `PIPELINE_VALIDATION_REQ`，其值为本 REQ id 字符串
`"REQ-validate-fresh-pipeline-1777123726"`。

- **不在生产路径被 import** —— `engine.py` / `router.py` / `actions/*` / `checkers/*` 都不引用
- **不导出到 `__init__`** —— 仓 root 没 `__init__.py`，其他模块也不会触达
- **没有 docstring 之外的副作用** —— 模块加载即定义一个 `str` 常量，无 IO / 无网络 / 无
  全局状态
- **future-proofing**：今后再来一次 fresh-pipeline smoke，只需新建一个新 REQ，把这个常
  量改成新 REQ id（一行 patch），即可重复使用模块作为脚手架

### contract 单测

`orchestrator/tests/test_contract_pipeline_marker.py` 用**白盒** import 验证：

1. 模块可被 import（不抛异常）
2. `PIPELINE_VALIDATION_REQ` 是字符串
3. 字符串值匹配 `^REQ-validate-fresh-pipeline-\d+$`（pattern 比硬编码值稳健，下次 smoke
   只需改 module 不改 test）
4. 模块没有副作用：`importlib.import_module` 后再 reload 仍得到同一个常量

仅 unit 套件（`make ci-unit-test`），**不**带 `@pytest.mark.integration` —— 本 REQ 不
碰 integration 集合。

## 取舍

- **为什么 `_` 前缀** —— 标记 "私有 / 不属于公共 API"，避免有人误以为这是 orchestrator
  对外约定的元数据；纯内部 smoke fixture
- **为什么不直接在 `pyproject.toml` 加 metadata** —— pyproject 改动会触发 build/wheel 路
  径敏感的 staging_test / accept compose；我们要的是 "代码加一个 byte 都不影响业务路径"
  的真 no-op，新增一个游离 .py 是面积最小的形式
- **为什么不 reuse 历史 REQ 的 marker** —— 历史 REQ 都有自己的业务负载（Makefile / 状态
  机变更 / accept stack），它们的 contract test 假设了那些负载存在；本 REQ 必须有一个
  **自己**的 contract artifact，否则 spec_lint 会因为"openspec change 内 zero scenarios
  ↔ test 引用"失败
- **为什么 spec 写在 capability `pipeline-marker` 而不是 `pipeline-validation`** —— 该
  capability 描述的就是一个 **marker 模块**的存在性契约；将来扩展 smoke fixture 也都
  归到这个 capability 下，名字更贴近实物
