"""Typed payload schemas for sisyphus stage I/O.

落地原则（见 docs/dispatch-contract.md）：

- intent.py: FinalizedIntent —— intake 输出 / analyze 直入消费 / runner clone 依据
- 不引 langgraph 等通用 workflow 框架；自家 state.py + pydantic schema 已够用
- schema 演进 doc-first：先在 docs/dispatch-contract.md §3 加字段说明，再改本目录 pydantic
"""
