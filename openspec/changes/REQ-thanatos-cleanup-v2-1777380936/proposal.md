# REQ-thanatos-cleanup-v2-1777380936: 清理 thanatos 死代码（descoped 模块全删除）

## 问题

thanatos MCP 验收层已于前期 descope（create_accept.py v0.3-lite 已移除 thanatos_block 解析），
但 thanatos/ 模块目录、CI workflow、helm chart、文档、测试引用、Makefile target、
未归档 openspec changes 残留仍留在仓内，形成死代码和误导性引用。

具体残留清单：
1. `thanatos/` —— 完整模块（src/、tests/、Dockerfile、pyproject.toml、uv.lock）
2. `.github/workflows/thanatos-ci.yml` —— thanatos 专属 CI workflow
3. `deploy/charts/thanatos/` —— helm chart
4. `docs/thanatos.md` —— 13KB thanatos 设计文档
5. `orchestrator/tests/test_contract_thanatos_ci.py` —— thanatos CI 合同测试（整文件）
6. `Makefile` —— ci-lint / ci-unit-test 中的 thanatos 段
7. `openspec/changes/REQ-thanatos-m0-scaffold-v6-1777283112/` —— 未归档 thanatos M0 scaffold change
8. `openspec/changes/REQ-ci-lint-test-thanatos-fix-1777338398/` —— 未归档 thanatos CI fix change
9. `scripts/example-reqs.yaml` —— 【缺口-2】描述过时（引用已不存在的 thanatos_block）

## 方案

**全删除，零新增代码。** 仅做减法：

1. `rm -rf thanatos/`
2. `rm .github/workflows/thanatos-ci.yml`
3. `rm -rf deploy/charts/thanatos/`
4. `rm docs/thanatos.md`
5. `rm orchestrator/tests/test_contract_thanatos_ci.py`
6. Makefile：ci-lint / ci-unit-test 去掉 thanatos 段
7. `rm -rf openspec/changes/REQ-thanatos-m0-scaffold-v6-1777283112/`
8. `rm -rf openspec/changes/REQ-ci-lint-test-thanatos-fix-1777338398/`
9. `scripts/example-reqs.yaml`：更新【缺口-2】描述

## 注意

- **保留** thanatos 相关的 openspec **已归档**变更记录（archive/ 中若有）
- **保留** create_accept.py 头部 "不接 thanatos MCP（descope）" 注释（当前行为描述）
- **保留** 历史 REQ proposal 中的 thanatos 背景引用（如 REQ-accept-m1-lite）
- **不修改** `docs/user-feedback-loop.md` 等设计文档中的 thanatos 概念引用（属架构历史记录）

## 验证

- `make ci-lint` 通过（orchestrator 范围）
- `make ci-unit-test` 通过（orchestrator 范围，无 thanatos）
- `grep -r "thanatos" --include="*.py" --include="*.yml" --include="*.yaml"` 仅命中历史文档/注释/配置
