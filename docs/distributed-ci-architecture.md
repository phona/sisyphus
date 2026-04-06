# 分布式 CI 架构设计

> 基于 n8n + vibe-kanban MCP + 测试机的 AI 驱动开发工作流

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              n8n (调度器)                                    │
│  职责: 阶段控制、决策判断、任务分发                                           │
│                                                                              │
│   Phase0 ──► Phase1 ──► Phase2 ──► Phase3 ──► Phase4 ──► Phase5 ──► Phase6  │
│   契约设计     三分拆分      并行开发      TDD        质量        验收        │
│                                                                              │
│   【任何阶段失败】                                                           │
│        │                                                                     │
│        └────► MCP: create_issue({title, desc, parent}) ──► vibe-kanban     │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ MCP Protocol
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       vibe-kanban (执行器)                                   │
│  职责: 接收任务、拆解子任务、调用 Claude 修复、git push、关闭 Issue          │
│                                                                              │
│   MCP Tools:                           内部执行流程:                         │
│   • create_issue()    ──► 接收任务    analyze ──► fix ──► verify ──► push   │
│   • read_file()       ──► 读取代码                                          │
│   • write_file()      ──► 修改代码                                          │
│   • execute_command() ──► git/make                                          │
│   • close_issue()     ──► 完成任务                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │ git push
                                       ▼
                              ┌─────────────────┐
                              │     Gitea       │
                              │  - 代码仓库     │
                              │  - Issues       │
                              └────────┬────────┘
                                       │ webhook
                                       ▼
                              ┌─────────────────┐
                              │     测试机       │
                              │   跑 CI 卡点     │
                              │   返回PASS/FAIL  │
                              └─────────────────┘
```

## 6 阶段工作流

| 阶段 | 名称 | 输入 | 输出 | 失败处理 |
|------|------|------|------|---------|
| **P0** | 契约设计 | 需求PRD | contract.spec.yaml | VK修改契约 |
| **P1** | OpenSpec拆分 | PRD+契约 | dev.spec / contract.spec / ac.spec | VK重新拆分 |
| **P2** | 并行开发 | 三分规格 | dev/test/ac三个分支 | VK修复对应分支 |
| **P3** | TDD Battle | 合并后的feature | 单元测试+契约测试通过 | VK修复代码 |
| **P4** | 质量关卡 | 通过TDD的代码 | Lint+AI Review通过 | VK修复规范 |
| **P5** | AI验收 | 通过质量的代码 | 验收用例通过 | VK修复业务逻辑 |
| **P6** | 发布 | 通过验收的代码 | 合并到master+部署 | - |

## 三分设计详情

### Phase1 输出（三份规格）

| 规格文件 | 内容 | 对应分支 | 负责角色 | 产出 |
|---------|------|---------|---------|------|
| **dev.spec.md** | 功能清单、数据模型、实现细节 | dev/REQ-01 | 开发 | 单元测试+实现代码 |
| **contract.spec.yaml** | API定义、请求/响应Schema、边界条件 | test/REQ-01 | 测试 | 契约测试 |
| **ac.spec.yaml** | Given/When/Then业务场景 | ac/REQ-01 | 验收方 | 验收用例yaml |

### Phase2 并行开发

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ dev/REQ-01  │    │ test/REQ-01 │    │ ac/REQ-01   │
│  开发负责    │    │  测试负责    │    │  验收负责    │
├─────────────┤    ├─────────────┤    ├─────────────┤
│ 1.写单元测试 │    │ 1.读契约规格 │    │ 1.读验收规格 │
│ 2.写实现代码 │    │ 2.写契约测试 │    │ 2.写yaml用例 │
│ 3.本地验证  │    │ 3.验证API   │    │ 3.验证可执行 │
│ 4.push分支  │    │ 4.push分支   │    │ 4.push分支   │
└─────────────┘    └─────────────┘    └─────────────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          ▼
                   合并到 feature/REQ-01
```

## 核心交互时序

```mermaid
sequenceDiagram
    actor Test as 测试机
    participant N8n as n8n调度器
    participant VK as vibe-kanban
    participant Claude as Claude MCP
    participant Gitea as Gitea

    Note over Test,N8n: CI 失败触发
    
    Test->>N8n: POST ci-failed(project, branch, failed_stage)
    N8n->>VK: MCP create_issue(title, desc, parent)
    activate VK
    VK-->>N8n: return issue_id VK-XXX
    deactivate N8n
    
    Note over VK,Claude: vibe-kanban 内部执行
    
    VK->>Claude: analyze_failure
    Claude-->>VK: 分析结果
    VK->>Claude: fix_code
    Claude-->>VK: 修复完成
    VK->>VK: execute_command(make ci-lint/test)
    VK->>Claude: commit_and_push
    Claude-->>VK: push完成
    VK->>Gitea: close_issue
    deactivate VK
    
    Note over Test,N8n: 修复完成重新CI
    Gitea->>Test: webhook push
    Test->>Test: 重新跑CI
    Test-->>N8n: CI Passed
```

## Issue 层级结构

```
REQ-01 (父需求 - n8n管理生命周期)
├── Phase0: 契约设计
│   └── VK-001: 设计契约规格
├── Phase1: OpenSpec拆分
│   ├── VK-002: 生成dev.spec.md
│   ├── VK-003: 生成contract.spec.yaml
│   └── VK-004: 生成ac.spec.yaml
├── Phase2: 并行开发
│   ├── VK-005: dev/REQ-01开发 (单元测试+实现)
│   ├── VK-006: test/REQ-01开发 (契约测试)
│   └── VK-007: ac/REQ-01开发 (验收用例)
├── Phase3: TDD Battle
│   ├── VK-008: Fix lint错误
│   ├── VK-009: Fix test错误
│   └── VK-010: Fix contract错误
├── Phase4: 质量关卡
│   └── VK-011: Fix quality问题
└── Phase5: AI验收
    └── VK-012: Fix acceptance失败
```

## 职责边界

| 组件 | 负责 | 不负责 |
|------|------|--------|
| **n8n** | 阶段控制、决策判断、调用VK | 不写代码 |
| **vibe-kanban** | 接收任务、拆解、调用Claude、git操作 | 不做阶段决策 |
| **Claude MCP** | 分析、写代码、修复 | 无状态，单次调用 |
| **测试机** | 跑CI测试、返回PASS/FAIL | 不改代码 |
| **Gitea** | 存储代码、Issue、触发CI | 不执行逻辑 |

## 实施阶段

见 [implementation-plan.md](./implementation-plan.md)

## 参考

- [AI 驱动测试工作流](./ai-driven-testing-workflow.md) - 详细阶段说明
- [n8n 工作流使用](./n8n-workflow-usage.md) - n8n 配置说明
