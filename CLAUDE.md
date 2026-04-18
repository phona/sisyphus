# Sisyphus

AI 驱动的无人值守开发平台。契约驱动 + 测试先行。

## 架构

架构文档为唯一权威参考：[docs/architecture.md](docs/architecture.md)

### 核心哲学

- **契约驱动（CDD）**：OpenAPI Spec 为唯一真相源
- **测试先行（TDD）**：先写测试，再写实现，测试 LOCKED 不可改
- **两段式流程**：有人阶段（需求分析，有歧义就停）→ 无人阶段（全自动，熔断兜底）

### 分工

| 角色 | 职责 |
|------|------|
| **n8n** | 门控：阶段串联、熔断、超时、可观测性 |
| **BKD** | 执行：每个 issue 是一个纯粹的单任务 |
| **OpenSpec** | 需求拆解：/opsx:propose |
| **aissh MCP** | 唯一桥梁：远程控制调试环境 |

### 阶段

```
需求分析 → 测试编写 → 开发 → 测试验证 → 验收 → review
```

每个阶段是一个独立的 BKD issue，通过 tag（REQ-xx）关联，n8n 通过 BKD webhook 事件驱动串联。

## 技术栈

- **编排**：n8n（vm-node04 K3s）
- **AI 执行**：BKD + Claude Agent（Coder Workspace）
- **需求拆解**：OpenSpec
- **远程控制**：aissh MCP
- **代码托管**：GitHub
- **调试环境**：K3s namespace 隔离

## 项目结构

```
sisyphus/
├── charts/n8n-workflows/   # n8n 工作流 JSON（v3-*.json）
├── docs/
│   ├── architecture.md      # 架构设计（权威）
│   ├── n8n-k3s-pitfalls.md  # n8n on K3s 踩坑手册
│   └── n8n-workflow-usage.md
├── testcases/               # 测试用例
└── Makefile
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [architecture.md](docs/architecture.md) | 架构设计 + 流程图（权威） |
| [n8n-k3s-pitfalls.md](docs/n8n-k3s-pitfalls.md) | n8n on K3s 踩坑手册（11 个坑）|
| [n8n-workflow-usage.md](docs/n8n-workflow-usage.md) | n8n 使用说明 |

## 开发规范

- 用中文交流
- 改完代码检查是否存在问题
- 每个流程节点必须有存在的理由，不搞花里胡哨
- 目标是加速开发，走向无人值守
