# ubox-crosser Management Platform

> Target repo: github.com/phona/ubox-crosser
> Objective: 从裸跑代理工具升级为可管理的代理平台，测试 sisyphus 多模块编排能力

## Requirements Index

| ID | Title | Priority | Difficulty | Scope | 测试维度 | Document |
|----|-------|----------|------------|-------|---------|----------|
| REQ-00 | 评估框架 | - | - | meta | 评分标准 | [REQ-00-evaluation-framework.md](./REQ-00-evaluation-framework.md) |
| REQ-01 | 共享协议层抽取 | P0 | Easy | crosser-proto | DAG 基线 | [REQ-01-proto-extraction.md](./REQ-01-proto-extraction.md) |
| REQ-02 | 数据面重构 | P0 | Hard | crosser-proxy | 并行+多文件 | [REQ-02-proxy-refactor.md](./REQ-02-proxy-refactor.md) |
| REQ-03 | 控制面 API 服务 | P0 | Hard | crosser-api | 并行+新模块 | [REQ-03-control-plane-api.md](./REQ-03-control-plane-api.md) |
| REQ-04 | 前端管理仪表盘 | P1 | Medium | crosser-web | 多技术栈 | [REQ-04-web-dashboard.md](./REQ-04-web-dashboard.md) |
| REQ-05 | 全栈集成与 CI | P1 | Medium | all | 胶水+E2E | [REQ-05-integration.md](./REQ-05-integration.md) |
| REQ-06 | 并发竞态修复 | P0 | Medium | 存量代码 | 迭代修复 | [REQ-06-bugfix-race-condition.md](./REQ-06-bugfix-race-condition.md) |
| REQ-07 | 并行任务文件冲突 | P1 | Medium | proto+proxy+api | 冲突预检 | [REQ-07-conflict-detection.md](./REQ-07-conflict-detection.md) |
| REQ-08 | 不完整需求容错 | P1 | Hard | crosser-api | 容错熔断 | [REQ-08-incomplete-requirement.md](./REQ-08-incomplete-requirement.md) |
| REQ-09 | 奇葩需求测试集 | P1 | Varies | all | 边界防御 | [REQ-09-absurd-requirements.md](./REQ-09-absurd-requirements.md) |
| REQ-10 | 性能基准测试 | P0 | - | meta | 速度/成本/质量 | [REQ-10-performance-benchmark.md](./REQ-10-performance-benchmark.md) |

## DAG Dependency

```
独立任务（可先行）:
  REQ-06 (bug fix, 存量代码)     ← 无依赖，测存量代码理解

主线任务:
  REQ-01 (proto)
    ├──→ REQ-02 (proxy)  ┐
    │                     ├── 并行，测 DAG 调度
    └──→ REQ-03 (api)    ┘
            └──→ REQ-04 (web)
                    └──→ REQ-05 (integration)

压力测试（依赖主线部分完成）:
  REQ-07 (conflict)              ← 依赖 REQ-01，测冲突预检
  REQ-08 (incomplete)            ← 依赖 REQ-03，测容错熔断
  REQ-09 (absurd)                ← 随时可测，测边界防御
```

## Target Architecture

```
ubox-crosser/
├── crosser-proto/      # 共享协议（go module）
│   ├── go.mod
│   └── message/
├── crosser-proxy/      # 数据面（重构后的 server/client/auth）
│   ├── go.mod
│   └── cmd/
├── crosser-api/        # 控制面（REST API + DB）
│   ├── go.mod
│   └── cmd/
├── crosser-web/        # 前端仪表盘
│   ├── package.json
│   └── src/
├── go.work             # Go workspace
├── docker-compose.yml  # 全栈编排
└── .github/workflows/  # CI
```

## By Difficulty

### Hard (3)
- REQ-02 数据面重构, REQ-03 控制面 API, REQ-08 不完整需求容错

### Medium (4)
- REQ-04 前端仪表盘, REQ-05 全栈集成, REQ-06 竞态修复, REQ-07 冲突检测

### Easy (1)
- REQ-01 协议层抽取

## 评估维度覆盖

| 维度 | 覆盖用例 |
|------|---------|
| DAG 编排 | REQ-01→02/03→04→05 |
| 并行执行 | REQ-02 ∥ REQ-03 |
| 多技术栈 | REQ-04 (React) + REQ-01/02/03 (Go) |
| 存量代码理解 | REQ-06 |
| 迭代修复/熔断 | REQ-06, REQ-08 |
| 冲突预检 | REQ-07 |
| 容错降级 | REQ-08 |
| 边界防御（奇葩需求） | REQ-09 (6 个子场景) |
| 性能（速度/成本/质量） | REQ-10 (效率比、吞吐量、稳定性) |
