# REQ-531: Descope IMPROVER daemon，文档化人工改进闭环

## 背景

`scripts/example-reqs.yaml` 将【缺口-5】描述为：
> "docs/architecture.md 写了 IMPROVER daemon（周期性跑 SQL → 生成 prompt 改进建议 → 自动提 PR），但代码库里零实现。"

经核实，`architecture.md` 并未承诺 IMPROVER daemon。它只是提到 `config_version` + `improvement_log` 两张表支撑"改动 → 度量"循环。而 `observability.md` 已明确改进闭环为**人工驱动**流程：
- 原则 #6："先被动报告，攒 2 周数据、误报率 < 5% 才升级"
- 可持续改进闭环：看板发现异常 → 诊断根因 → 生成假设 → 改 prompt → bump `config_version` → 等 2 周 → 回填 `verdict`

## 决策

**B. Descope IMPROVER daemon，更新文档消除误解。**

### 不实现自动化的理由

1. **数据量不足**：日处理约 5 REQ，统计显著性不够支撑自动化决策
2. **prompt 还在快速迭代**：M14→M18 刚完成 verifier 框架、analyze 全责交付、challenger，prompt 结构不稳定
3. **自动改 prompt 风险极高**：prompt 是系统核心资产，改坏影响所有 REQ 质量
4. **现有工具已构成有效人工闭环**：`config_version` + `improvement_log` + 18 条 SQL + Metabase 看板
5. **符合 sisyphus 设计原则**："先被动报告再主动干预"、"薄编排"

## 范围

- 更新 `docs/architecture.md`：§10 核心准则段落明确人工驱动；§12 添加 descope 说明；§0.4 标题去混淆
- 更新 `docs/IMPACT-REPORT.md`：修正 `improvement_log` 描述
- 确保无残留 IMPROVER daemon 引用

## 验收

- [x] 决策文档化（本 proposal + architecture.md 中的 descope 说明）
- [x] 文档更新，无残留引用
- [x] 无新增服务代码（descope 不引入实现）
