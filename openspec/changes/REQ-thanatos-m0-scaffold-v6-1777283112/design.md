# design — REQ-thanatos-m0-scaffold-v6-1777283112

权威设计在仓库根 [`docs/thanatos.md`](../../../docs/thanatos.md)（M0 同步入仓）。
本文档记录把那个设计落到代码 / spec / chart 时的关键决策，给 reviewer 跟 future
maintainer 留 paper trail。

## 决策 1 ─ thanatos 不写 `thanatos/Makefile`，由顶层 Makefile 串 ci-* 三条

**Why**：`ci-lint` / `ci-unit-test` / `ci-integration-test` 是
[docs/integration-contracts.md](../../../docs/integration-contracts.md) 给**独立
source repo** 的契约（sisyphus 机械 checker `dev_cross_check` / `staging_test` 跑
`/workspace/source/<repo>/Makefile` 的这三个 target）。thanatos 不是独立 repo，
它住 sisyphus 仓内，跟 orchestrator 同级。

如果 thanatos 自己有 Makefile 但顶层 Makefile 没调它，那它的 lint / test 完全不
在 sisyphus CI 路径上 = 跟没 CI 一样。

**怎么落**：顶层 `Makefile` 既有 `ci-lint` 在 `cd orchestrator && uv run ruff
check ...` 之后多加一行 `cd thanatos && uv run ruff check src/ tests/`，
`ci-unit-test` 同理多加 `cd thanatos && uv run pytest -m "not integration"`，
`ci-integration-test` 同理多加 `cd thanatos && uv run pytest -m integration` 并
沿用顶层既有 "exit 5 视为 pass" pattern（M0 thanatos 没 integration test，pytest
exit 5 等于 pass）。

`thanatos/pyproject.toml` 仍单独保留：以后装 playwright/chromium / adb-tools 不
污染 orchestrator 环境。

## 决策 2 ─ driver 全 `raise NotImplementedError("M0: scaffold only")`

**Why**：避免"空函数体 silent-pass"。M1 真填 driver 实现时，任何忘补的方法直接
炸一个非常清晰的 `NotImplementedError`，不会让 acceptance 跑过 stub 路径误以为
pass。错误信息固定字符串 `"M0: scaffold only"` 也方便 grep。

**Tradeoff**：丧失"任何方法可被 mock 替换"的灵活度——不接受。M0 测试不需要
mock driver，scenario parser + skill loader 是测试焦点。

## 决策 3 ─ MCP SDK 用官方 `mcp>=1.2`

**Why**：[`orchestrator/pyproject.toml`](../../../orchestrator/pyproject.toml)
已经声明 `mcp>=1.2`，整仓只一份依赖。手写 JSON-RPC 没收益，徒增维护成本。

## 决策 4 ─ scenario parser 真业务码 + ≥10 测试 case

**Why**：parser 是 M1+ driver 跑通 GIVEN/WHEN/THEN 的喂料源。设计上要兼容两种
spec 格式（gherkin code-block / markdown bullet）。这两个格式都已经在 sisyphus 现
有 spec.md 里出现，提前测够省 M1 debug 时间。

**测试覆盖**（详见 `thanatos/tests/test_scenario_parser.py`）：
- gherkin 单 scenario / 多 scenario / Given-And-When-And-Then 链 / 大小写不敏感
- bullet 单 / 多 / 多 GIVEN-WHEN-THEN 累加
- 错误：mixed 格式 / 重复 id / 空块
- 边界：`#### Scenario:` 出现在 fenced code block 内被忽略 / 中文 unicode 描述

## 决策 5 ─ helm chart `.Values.driver` 用 `{{ fail }}` 守卫，不要静默回 default

**Why**：无效 driver 值（`desktop` / 拼错 / 空字符串）必须立刻让 `helm template`
红，不然装出去得到一个空 Pod，accept 阶段才发现就晚了。`templates/_helpers.tpl`
的 `thanatos.assertDriver` 模板做这个守门 —— 每个 yaml template 都先 include
它。同时 driver=adb 时 `redroid.image` 不能空（同样 `fail`）。

## 决策 6 ─ `service.yaml` 留下但只 debug 用

**Why**：accept-agent 走 `kubectl exec` 直接拿 stdio MCP 流，不通过 service。
但保留 `ClusterIP` 的 service 让开发本地能 `kubectl port-forward svc/thanatos`
调一个 MCP 客户端做 sanity check —— 不留就要每次手写 Service yaml。

## 决策 7 ─ M0 不发 OCI，chart 不 push registry

**Why**：thanatos:dev 镜像 build 出来后只在 K3s pod 内用 `python -m
thanatos.server` 跑。没有 chart 仓 / OCI 注册需求，留在仓内 `deploy/charts/`
即可。M1 业务仓 `accept-env-up` 接 `helm install` 时再决定要不要发 OCI。

## 决策 8 ─ `involved_repos: []` 走 helm L4 兜底

**Why**：sisyphus dogfood 部署的 `values.yaml` 里
`default_involved_repos: [phona/sisyphus]` 是单仓部署的 boilerplate 兜底。本
REQ 涉及单仓 phona/sisyphus，显式写 `["phona/sisyphus"]` 跟兜底重复。intake JSON
schema 强 require 6 字段但允许空 list。

**长远**：sisyphus 切多仓部署时这个字段会重新有意义。M0 不为多仓部署提前防御。

## 不做（明确砍掉）

- 不动 `accept.md.j2` / state machine / actions / checkers / verifier prompt /
  runner Dockerfile —— M0 不接 accept stage 调用链
- 不写真实 driver 运行时（playwright spawn chromium / adb shell / http client）
- 不写 preflight 节点数 / a11y tree 探查
- 不写 screenshot 兜底
- 不写 kb_updates 真生成（`run_scenario` 永远返回 `kb_updates: []`）
- 不写 recall 真实索引（永远返回 `[]`）
- 不写业务仓 GHA "thanatos lint" 强制全量 a11y
- 不引入新 stage / state / event / mechanical checker / verifier 模板
