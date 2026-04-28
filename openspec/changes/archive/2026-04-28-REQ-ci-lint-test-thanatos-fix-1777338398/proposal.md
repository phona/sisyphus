# Proposal: fix(ci): thanatos 阻塞所有 PR 的 lint-test ModuleNotFoundError

## 问题

PR #168（thanatos M0 scaffold）合并后，root `Makefile` 的 `ci-integration-test` target
在末尾新增了一段运行 `cd thanatos && uv run pytest -m integration` 的 recipe：

```makefile
@cd $(SCRIPT_DIR)/thanatos && set +e; uv run pytest -m integration; rc=$$?; \
if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi; \
...
exit 0
```

但 `thanatos/pyproject.toml` 的 dev 工具（pytest、ruff 等）仅定义在
`[project.optional-dependencies].dev`，而不是 `[dependency-groups].dev`。
`uv run pytest` 在 thanatos 目录下找不到 pytest，fallback 到 one-off tool 模式——
该模式的运行环境里没有 thanatos 包，导致：

```
ModuleNotFoundError: No module named 'thanatos'
make: *** [Makefile:56: ci-integration-test] Error 2
```

exit 2（collection error）不是 exit 5，`ci-integration-test` 的 "exit 5 → pass" 逻辑
无法捕获，make 报错退出。这导致 `test_MFCT_S6_ci_integration_test_zero_tests_exits_0`
合规测试失败，进而连环炸 PR #170 #172 #179 的 CI。

## 方案（Option B）

**root Makefile `ci-integration-test` 去掉 thanatos 段**：thanatos 自带独立 CI
（`thanatos-ci.yml`）覆盖 lint + unit test，integration test 在 thanatos 自身 CI 中管理，
不需要从 root Makefile 重复跑。去掉后：
- `make ci-integration-test` 只跑 orchestrator 的集成测试（PostgreSQL 不通时 exit 0）
- exit 5 → pass 逻辑保留
- MFCT-S6 通过

**附带修复**：
1. `thanatos/pyproject.toml` 新增 `[dependency-groups].dev`，使 `uv run pytest` /
   `uv run ruff check` 能在 thanatos 目录下不依赖 one-off tool 模式正常工作（修复
   `ci-unit-test` 的同类隐患）。
2. `.github/workflows/thanatos-ci.yml` 新建，让 `thanatos/**` 变更的 PR 有 GHA
   check-runs，`pr_ci_watch` 不再报 no-gha。
