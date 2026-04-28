# REQ-528: 清理 thanatos 死代码

## 背景

thanatos MCP 已 descope（accept stage v0.3-lite 改为纯 shell 流程，不再通过 thanatos MCP 驱动 acceptance scenario）。但代码库中仍有 thanatos 相关死代码残留：

- `create_accept.py` docstring 引用 thanatos descope
- `tests/` 中 4 个 thanatos 测试文件（含 mock 和 contract test）
- `accept.md.j2` 中 thanatos MCP 分支（已在 prior commit 中清理）

## 范围

1. 删除 `orchestrator/tests/test_contract_thanatos_ci.py`
2. 删除 `orchestrator/tests/test_create_accept_thanatos.py`
3. 删除 `orchestrator/tests/test_contract_thanatos_mcp_wire_challenger.py`
4. 删除 `orchestrator/tests/test_prompts_accept_thanatos.py`
5. 清理 `create_accept.py` docstring 中的 thanatos 引用
6. 确认 `accept.md.j2` thanatos 分支已清理（prior commit 已完成）

## 验收标准

- thanatos 在 `orchestrator/src/` 中零业务引用
- thanatos 在 `orchestrator/tests/` 中零文件
- `make ci-lint` pass
- `make ci-unit-test` pass（排除 thanatos 测试后的现有测试集）
- `make ci-integration-test` pass
