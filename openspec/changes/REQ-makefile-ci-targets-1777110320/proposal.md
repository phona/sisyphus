# REQ-makefile-ci-targets-1777110320: feat(Makefile): add ci-lint/ci-unit-test/ci-integration-test for self-dogfood

## 问题

[docs/integration-contracts.md](../../../docs/integration-contracts.md) 把 `ci-lint` / `ci-unit-test` /
`ci-integration-test` 立成 source repo 接入 sisyphus 的硬契约 ——
`dev_cross_check` checker 在 runner pod 跑 `BASE_REV=$(git merge-base HEAD origin/main) make ci-lint`，
`staging_test` checker 跑 `make ci-unit-test && make ci-integration-test`，且两个目标
**都必须存在**否则该仓被 skip（`grep -q '^ci-unit-test:' Makefile && grep -q '^ci-integration-test:' Makefile`）。

但 sisyphus 仓自己的根 Makefile 只有 `dev-cross-check` (ruff) 和 `ci-test` (pytest)，名字不对、缺
`ci-integration-test`。结果 sisyphus 改自己代码时**没法走 sisyphus 自己的 dev_cross_check / staging_test
checker**——self-dogfood 跑不通，等于 "我们自己造的轮子，自己装不上车"。

## 根因

历史命名跟 ttpos-ci 标准发散：

- 顶层 Makefile 写的是 `dev-cross-check` (ruff) + `ci-test` (pytest) ——
  在 ttpos-ci 出现以前的本地约定，没跟 [docs/integration-contracts.md §2.1](../../../docs/integration-contracts.md) 对齐
- `ci-lint` 缺了 `BASE_REV` 语义（dev_cross_check 期望仅 lint 变更文件）
- `ci-integration-test` target 完全不存在，staging_test 看到该仓没这个 target 就跳过整仓

## 方案

把顶层 Makefile 改造成 ttpos-ci 标准 source repo：

### `ci-lint`（替代 `dev-cross-check`）

- 接受 `BASE_REV` env 变量；非空时仅 lint `BASE_REV..HEAD` 之间变更的 Python 文件（限定在
  `orchestrator/src` + `orchestrator/tests` 子树）；空字符串或没变更文件时退化为全量 ruff
- 保持 `cd orchestrator && uv run ruff check` 的实现（ruff 已是项目唯一 lint 工具）
- 退码 0 = pass，跟 dev_cross_check `make ci-lint` 退码契约对齐

### `ci-unit-test`（替代 `ci-test`）

- `cd orchestrator && uv run pytest -m "not integration"`，等价当前 500 个 unit-shaped 测试
- 加 `integration` 自定义 marker 注册（pyproject.toml `[tool.pytest.ini_options].markers`）
  避免 PytestUnknownMarkWarning + 给 ci-integration-test 用

### `ci-integration-test`（新增）

- `cd orchestrator && uv run pytest -m integration`
- 当前没有任何测试带 `integration` marker → pytest 退码 5（no tests collected）
- target 包装：`exit_code=$? && [ $exit_code -eq 0 -o $exit_code -eq 5 ]`，退码 5 视为 pass
  （placeholder 语义：今天 sisyphus 没起 docker compose 的 integration 套件；
  marker 已注册，未来 PR 加 `@pytest.mark.integration` 测试时这个 target 自动开始跑实测）
- **不要求今天就有真实集成测试** —— 自 dogfood 只要求 target 存在 + 退码 0

### 删历史命名

`dev-cross-check` / `ci-test` 在 sisyphus 仓内 **零外部 callers**（grep 全仓 `make dev-cross-check` / `make ci-test` 0 命中），
直接删掉，避免双轨维护。

## 取舍

- **为什么 `ci-integration-test` 没 docker compose stack** —— sisyphus orchestrator 本身是
  无外部依赖的纯 mock 测试，没有"启 stack 跑端到端"的天然边界；强加一个 docker compose
  装样子反而拖慢 staging_test。今天的契约是"target 存在 + 0 退码"，未来真有集成场景
  （例：跑真 Postgres + yoyo migrations 验 schema）时再加 `@pytest.mark.integration` 测试。
- **为什么 BASE_REV 仅作用于 `*.py`** —— ruff 只能 lint Python；`*.md` / 文档 / 配置文件改动
  不该让 ci-lint 失败。
- **为什么不加 helm lint / shellcheck** —— 业务 repo 的 ci-lint 也只是 `golangci-lint`，没
  把 helm / shell 也纳入；保持一致。`.github/workflows/orchestrator-ci.yml` 已有 helm-lint
  独立 job，sisyphus 自己 PR 就能跑到。
