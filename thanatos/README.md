# thanatos — sisyphus 验收能力层

> M0 scaffold only。运行时 driver 全部未实现；只有 scenario parser + skill loader 是真代码。

设计权威：[`docs/thanatos.md`](../docs/thanatos.md)（仓库根级，由 sisyphus engine 共享）。

## M0 范围

- ✅ MCP stdio server entrypoint（`python -m thanatos.server`）—— 注册 3 个 tool，全 stub
- ✅ scenario parser（`thanatos.scenario`）—— 解析 spec.md 里 `#### Scenario:` 块的 gherkin / bullet 两种格式
- ✅ skill loader（`thanatos.skill`）—— 校验 `.thanatos/skill.yaml`（必填 `driver` / `entry`，driver ∈ {playwright, adb, http}）
- ✅ Driver Protocol（`thanatos.drivers.base`）—— `preflight / observe / act / assert_ / capture_evidence` 五方法 async 契约
- ❌ Driver 实现（playwright / adb / http）—— 全部 `raise NotImplementedError("M0: scaffold only")`
- ❌ KB 更新真实生成（`run_scenario` 永远返回 `kb_updates: []`）
- ❌ recall 索引（永远返回 `[]`）

## Build / test / run

```bash
# lint
cd thanatos && uv run ruff check src/ tests/

# unit tests（≥10 parser case + skill loader case）
cd thanatos && uv run pytest -m "not integration"

# 镜像 build
docker build thanatos/ -t thanatos:dev

# MCP banner sanity check
docker run --rm -i thanatos:dev python -m thanatos.server <<< '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

顶层 sisyphus Makefile 已经把 thanatos 的 lint / unit-test / integration-test 串进 `make ci-lint / ci-unit-test / ci-integration-test` —— 跟 orchestrator 一起被 sisyphus 自己的 staging_test / dev_cross_check 覆盖。
