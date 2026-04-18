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
├── values/              # 配置
├── docs/                # 文档
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
| [workflow-v2-architecture.md](docs/workflow-v2-architecture.md) | V2 架构设计（权威参考） |
| [workflow-v3-flow.md](docs/workflow-v3-flow.md) | V3 工作流全景 |
| [bkd-agent-prompt-template.md](docs/bkd-agent-prompt-template.md) | BKD Agent Prompt 模板 |
| [n8n-workflow-usage.md](docs/n8n-workflow-usage.md) | n8n 主编排使用说明 |
| [api-tag-management-spec.md](docs/api-tag-management-spec.md) | API 标签管理规范 |
