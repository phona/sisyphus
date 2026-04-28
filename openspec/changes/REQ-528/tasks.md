# REQ-528 Tasks

## Stage: cleanup

- [x] 删除 `orchestrator/tests/test_contract_thanatos_ci.py`
- [x] 删除 `orchestrator/tests/test_create_accept_thanatos.py`
- [x] 删除 `orchestrator/tests/test_contract_thanatos_mcp_wire_challenger.py`
- [x] 删除 `orchestrator/tests/test_prompts_accept_thanatos.py`
- [x] 清理 `create_accept.py` docstring thanatos 引用
- [x] 验证 `accept.md.j2` thanatos 分支已清理

## Stage: verification

- [x] `ruff check src/ tests/` pass
- [x] `pytest -m "not integration"` pass (1865 passed)
- [x] `make ci-lint` pass
- [x] `make ci-integration-test` pass (PostgreSQL unreachable → exit 5 → pass)

## Stage: PR

- [ ] push feat/REQ-528
- [ ] 开 PR（label: sisyphus）
