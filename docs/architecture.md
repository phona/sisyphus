# Sisyphus 架构设计

> 契约驱动 + 测试先行的 AI 无人值守开发平台。

## 核心哲学

```
有人阶段（人机协作）           无人阶段（全自动）
需求分析 → 测试编写      →    开发 → 测试验证 → 验收
有歧义就停    测试锁定          熔断兜底
```

- **契约驱动（CDD）**：OpenAPI Spec 为唯一真相源
- **测试先行（TDD）**：先写测试再写实现，测试 LOCKED 不可改
- **每个阶段只做一件事**：产出物明确，职责不交叉

## 五阶段流程

```mermaid
flowchart LR
    A[需求分析] -->|openspec artifacts| B[测试编写]
    B -->|contract_test + acceptance_test LOCKED| C[开发]
    C -->|业务代码 + 单元测试| D[测试验证]
    D -->|通过| E[验收]
    D -->|失败| C
    E -->|通过| F[Review]
    E -->|失败| C

    style A fill:#e3f2fd
    style B fill:#e3f2fd
    style C fill:#e8f5e9
    style D fill:#fff3e0
    style E fill:#fff3e0
    style F fill:#f3e5f5
```

### 阶段一：需求分析（有人）

| 项 | 说明 |
|---|------|
| **输入** | 需求描述 |
| **做什么** | /opsx:propose 拆解需求，设计合约边界 |
| **产出** | openspec/changes/xxx/ (proposal.md, specs/, design.md, tasks.md) |
| **不做** | 不写代码，不写测试 |
| **歧义** | 停下来问用户 |

### 阶段二：测试编写（有人）

| 项 | 说明 |
|---|------|
| **输入** | openspec artifacts + contract.spec.yaml |
| **做什么** | 写契约测试代码 + 验收测试代码 |
| **产出** | contract_test.* + acceptance_test.* |
| **不做** | 不写业务代码 |
| **LOCKED** | 测试产出后锁定，后续阶段不可修改 |
| **歧义** | 停下来问用户（最后的人工关卡） |

### 阶段三：开发（无人）

| 项 | 说明 |
|---|------|
| **输入** | openspec artifacts + 测试代码（只读） |
| **做什么** | 按 tasks.md 写业务代码 + 单元测试 |
| **产出** | 业务代码 + 单元测试 |
| **不做** | 不修改 contract_test / acceptance_test / contract.spec |
| **完成** | commit + push |

### 阶段四：测试验证（无人）

| 项 | 说明 |
|---|------|
| **输入** | 代码 + 所有测试 |
| **做什么** | 在调试环境跑分层测试 |
| **分层** | L0 lint → L1 单元测试 → L2 契约测试 → L3 集成测试 |
| **不做** | 不改代码，只报告结果 |
| **结果** | 通过 → 验收，失败 → 回开发 |

### 阶段五：验收（无人）

| 项 | 说明 |
|---|------|
| **输入** | acceptance_test.* + 干净环境 |
| **执行者** | 独立 agent，无开发上下文 |
| **做什么** | 部署完整环境，跑验收测试 |
| **不做** | 不改代码，不改测试 |
| **结果** | 通过 → review，失败 → 回开发 |

## 系统协作

```mermaid
sequenceDiagram
    participant User as 用户
    participant BKD as BKD
    participant N8N as n8n
    participant Agent as Agent
    participant Debug as 调试环境

    User->>BKD: 创建需求 Issue
    BKD->>N8N: /v2 触发
    N8N->>BKD: 创建 [REQ-xx] 需求分析 issue

    Note over N8N,Agent: 阶段一：需求分析
    Agent->>Agent: /opsx:propose
    Agent->>BKD: 移 review
    BKD-->>N8N: webhook (title 含 "需求分析")

    Note over N8N,Agent: 阶段二：测试编写
    N8N->>BKD: 创建 [REQ-xx] 测试编写 issue
    Agent->>Agent: 写 contract_test + acceptance_test
    Agent->>BKD: 移 review
    BKD-->>N8N: webhook (title 含 "测试编写")

    Note over N8N,Debug: 阶段三：开发
    N8N->>BKD: 创建 [REQ-xx] 开发 issue
    Agent->>Agent: 写业务代码
    Agent->>BKD: 移 review
    BKD-->>N8N: webhook (title 含 "开发")

    Note over N8N,Debug: 阶段四：测试验证
    N8N->>BKD: 创建 [REQ-xx] 测试验证 issue
    Agent->>Debug: aissh: 跑 L0-L3
    Debug-->>Agent: 结果
    Agent->>BKD: 移 review
    BKD-->>N8N: webhook (title 含 "测试验证")

    alt 测试失败
        N8N->>N8N: 熔断检查
        N8N->>BKD: 创建新的 开发 issue（带失败上下文）
    end

    Note over N8N,Debug: 阶段五：验收
    N8N->>BKD: 创建 [REQ-xx] 验收 issue
    Agent->>Debug: 部署 + 跑 acceptance_test
    Agent->>BKD: 移 review
    BKD-->>N8N: webhook (title 含 "验收")

    alt 验收失败
        N8N->>BKD: 回到开发阶段
    end

    N8N->>BKD: 父 Issue 设 review
    BKD-->>User: 完成通知
```

## Issue 关联与可观测性

同一需求的所有 issue 通过 **tag** 和 **父 issue follow-up** 关联：

```
父 Issue: "实现 /api/connections 接口"
  ├── [REQ-xx] 需求分析    (tags: REQ-xx, analyze)
  ├── [REQ-xx] 测试编写    (tags: REQ-xx, test-write)
  ├── [REQ-xx] 开发        (tags: REQ-xx, dev)
  ├── [REQ-xx] 测试验证    (tags: REQ-xx, verify)
  └── [REQ-xx] 验收        (tags: REQ-xx, accept)
```

**三层可观测性**：
- **BKD 看板**：按 REQ-xx tag 看完整链路，每个 issue 有完整对话日志
- **n8n execution**：每个阶段独立记录，耗时、成功率、失败原因
- **父 issue 时间线**：n8n 每个阶段完成后 follow-up 进展摘要

## n8n 架构

**2 个 webhook**，纯事件驱动：

```mermaid
flowchart TB
    subgraph ENTRY["/v2 入口"]
        E1[Webhook] --> E2[MCP Init + Set Ctx]
        E2 --> E3[Create 需求分析 Issue]
        E3 --> E4[Follow-up + Start]
        E4 --> E5[Callback 父 Issue]
    end

    subgraph EVENTS["/bkd-events 路由"]
        V1[Webhook] --> V2[MCP Init + Set Ctx]
        V2 --> V3{按 title 路由}
        V3 -->|需求分析完成| CREATE_TW[创建 测试编写 issue]
        V3 -->|测试编写完成| CREATE_DEV[创建 开发 issue]
        V3 -->|开发完成| CREATE_VER[创建 测试验证 issue]
        V3 -->|测试验证 通过| CREATE_ACC[创建 验收 issue]
        V3 -->|测试验证 失败| FUSE{熔断检查}
        FUSE -->|未触发| RETRY[创建新 开发 issue]
        FUSE -->|触发| ESCALATE[升级人工]
        V3 -->|验收 通过| DONE[父 Issue review]
        V3 -->|验收 失败| RETRY
    end
```

## BKD Webhook

```json
{
  "url": "http://n8n.43.239.84.24.nip.io/webhook/bkd-events",
  "events": ["issue.status.review", "session.completed", "session.failed"]
}
```

n8n 通过 `title` 关键词路由：需求分析 → 测试编写 → 开发 → 测试验证 → 验收。

## 分工

| 角色 | 做什么 | 不做什么 |
|------|--------|---------|
| **n8n** | 阶段串联、创建 issue、熔断、超时、可观测性 | 不做 AI 判断、不管执行细节 |
| **BKD** | 管理 issue、启动 agent、webhook 通知 | 不做阶段决策 |
| **Agent** | 执行单个任务、产出交付物、完成后移 review | 不做跨阶段编排 |
| **OpenSpec** | 需求拆解（/opsx:propose） | — |
| **aissh MCP** | 远程控制调试环境 | 不做逻辑判断 |

## 熔断

```
测试验证或验收失败 → n8n 检查：
  轮次 ≥ 3 → 升级人工
  超时 → 升级人工
  token 超限 → 升级人工
  否则 → 创建新的开发 issue（带失败上下文）→ 重试
```

## 数据流

| 通道 | 用途 |
|------|------|
| n8n `/v2` | 入口触发 |
| BKD webhook → n8n `/bkd-events` | 阶段完成通知（事件驱动） |
| BKD MCP | n8n 创建/管理 issue |
| git push/pull | 代码传输 |
| aissh MCP | 远程控制调试环境 |
| BKD issue 对话 | 可观测性 + 人机交互 |
