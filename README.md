# Sisyphus

AI 驱动的无人值守开发平台。

## 架构

三环模型：开发环境（写代码+控制）→ 调试环境（验证）→ GitHub（最终门禁）

- **编排**：n8n
- **AI 执行**：BKD + Claude Agent
- **远程控制**：aissh MCP
- **CI/CD**：GitHub Actions

详见 [V2 架构设计](docs/workflow-v2-architecture.md)

## 项目结构

```
sisyphus/
├── charts/
│   └── n8n-workflows/   # n8n 工作流 JSON
├── values/              # helm values（n8n / postgresql / metabase）
├── docs/                # 文档
├── observability/       # Postgres schema + Metabase 查询（可观测性）
├── router/              # Router 纯函数 + 单测
├── scripts/             # sidecar linter
├── projects/            # 子项目（git submodule）
│   ├── ttpos-flutter/
│   ├── ttpos-server-go/
│   └── vibe-kanban/
├── testcases/           # 测试用例
└── Makefile             # 统一命令入口
```

## 常用命令

```bash
make help          # 显示所有命令
make test-all      # 运行所有项目测试
make test-flutter  # 运行 Flutter 测试
make test-go       # 运行 Go 测试
```

## 文档

| 文档 | 内容 |
|------|------|
| [architecture.md](docs/architecture.md) | 架构设计（权威参考） |
| [observability.md](docs/observability.md) | 可观测性设计（Postgres + Metabase + n8n tap） |
| [workflow-current.md](docs/workflow-current.md) | 当前工作流状态（v3.1） |
| [prompts.md](docs/prompts.md) | 各阶段 agent prompt 大全 |
| [n8n-workflow-usage.md](docs/n8n-workflow-usage.md) | n8n 主编排使用说明 |
| [n8n-k3s-pitfalls.md](docs/n8n-k3s-pitfalls.md) | n8n on K3s 踩坑手册 |
| [api-tag-management-spec.md](docs/api-tag-management-spec.md) | API 标签管理规范 |
| [observability/README.md](observability/README.md) | 观测系统部署 / 运维 |
