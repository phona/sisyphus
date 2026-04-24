# sisyphus

AI-native CI 编排层。状态机 + 机械 checker + 度量，管理 analyze→spec→dev→staging-test→pr-ci→accept→archive 流水线。

## 模块

| 模块 | 路径 | 说明 |
|------|------|------|
| Orchestrator | `orchestrator/` | Python 状态机 + 路由 + action handlers |
| Runner | `runner/` | K8s runner pod 镜像 |
| Docs | `docs/` | 接入契约、架构文档 |

## 开发规范

- 接入契约文档：`docs/integration-contracts.md`
- 状态机权威：`docs/state-machine.md`
- action 注册：`orchestrator/src/orchestrator/actions/`
