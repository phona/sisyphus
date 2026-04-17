# Sisyphus

AI 驱动的无人值守开发平台。

## 架构

V2 架构文档为唯一权威参考：[docs/workflow-v2-architecture.md](docs/workflow-v2-architecture.md)

### 三环模型

- **开发环境**：AI 写代码（worktree 隔离），n8n 主编排，通过 aissh MCP 操控调试环境
- **调试环境**：K8s namespace 隔离，纯被动执行，分层验证（静态检查→单测→契约→集成）
- **GitHub**：最终门禁，CI 全量一把梭，独立子 agent 做验收测试

### 硬约束

- 开发环境只通过 **aissh MCP** 连接调试环境，这是唯一桥梁
- 开发环境**只写代码**，所有验证都推到调试环境跑
- 代码传输走 **git push/pull**，控制走 **aissh MCP**

### 两层编排

- **n8n**：粗粒度主编排，管阶段推进、熔断、并发池、冲突预检
- **AI skill**：细粒度小编排，管具体实现，有心跳上报和硬超时兜底

### 关键机制

- 熔断：≥3轮修复 or 超时 or token 超限 → 挂 Issue 升级人工
- 心跳：AI skill 执行时定期上报，超时强制中断
- 并发池：控制同时活跃的 namespace 数量
- 冲突预检：分发前检查文件级冲突
- 串行合并窗口：避免并行 PR 互踩
- CI 失败分类：flaky test 直接 retry，真实失败回开发环境
- Issue 可观测性：记录失败层级、原因、修复 diff、token 消耗、耗时、资源使用

## 技术栈

- **编排**：n8n
- **AI 执行**：vibe-kanban + Claude MCP
- **远程控制**：aissh MCP
- **代码托管**：Gitea（开发阶段）→ GitHub（生产）
- **基础设施**：Kind (K8s) + PostgreSQL + Helm
- **CI/CD**：GitHub Actions

## 项目结构

```
sisyphus/
├── charts/              # Helm charts（PostgreSQL, Gitea, n8n, vibe-kanban）
├── values/              # Helm values 配置
├── scripts/             # 部署脚本
├── docs/                # 文档
│   ├── workflow-v2-architecture.md  # V2 架构（权威）
│   └── ...              # 其他参考文档
├── projects/            # 子项目（git submodule）
│   ├── ttpos-flutter/
│   ├── ttpos-server-go/
│   └── vibe-kanban/
├── testcases/           # 测试用例
├── Makefile             # 统一命令入口
├── start.sh             # 一键启动
└── kind-config.yaml     # K8s 集群配置
```

## 文档索引

### 当前有效

| 文档 | 内容 |
|------|------|
| [workflow-v2-architecture.md](docs/workflow-v2-architecture.md) | V2 架构设计（权威参考） |
| [api-tag-management-spec.md](docs/api-tag-management-spec.md) | API 标签管理规范，对应 P0 合约阶段 |
| [api-lifecycle-management.md](docs/api-lifecycle-management.md) | API 生命周期管理 |
| [workflow-apifox-contract-testing.md](docs/workflow-apifox-contract-testing.md) | 契约测试实践 |
| [vibe-kanban-manual-build.md](docs/vibe-kanban-manual-build.md) | vibe-kanban 构建指南 |
| [n8n-workflow-usage.md](docs/n8n-workflow-usage.md) | n8n 主编排使用说明 |


## 开发规范

- 用中文交流
- 改完代码检查是否存在问题
- 每个流程节点必须有存在的理由，不搞花里胡哨
- 目标是加速开发，走向无人值守
