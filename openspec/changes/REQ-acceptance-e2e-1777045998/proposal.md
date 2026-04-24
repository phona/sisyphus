# Proposal: REQ-acceptance-e2e-1777045998

## Summary

修复 `docs/integration-contracts.md` 中关于 integration repo 目标名称的错误文档：将 `accept-up`/`accept-down` 更新为 `ci-accept-env-up`/`ci-accept-env-down`（与 `create_accept.py` 和 `teardown_accept_env.py` 实际调用的目标名一致）；将 Helm-based 模板替换为 Docker Compose 模板（runner pod 无 kubectl）。

## Motivation

1. **文档与代码不一致**：`create_accept.py` 调用 `make ci-accept-env-up`，但文档写的是 `accept-up`，导致业务 repo 实现者按文档做了一个永远不会被调用的 target
2. **Helm 模板不适用**：sisyphus runner pod 只有 Docker DinD，没有 kubectl/helm，文档中的 Helm 例子无法在 runner 中运行

## Approach

在 `docs/integration-contracts.md` 中：
1. 全文将 `accept-up` → `ci-accept-env-up`，`accept-down` → `ci-accept-env-down`
2. §4.2 integration repo 模板改为 Docker Compose 例（bash accept/env-up.sh 风格）
3. §3 章节标题、§5 env 表格、§8 排查清单对应更新
4. 添加 runner pod 无 kubectl 的说明
