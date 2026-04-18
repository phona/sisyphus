# V3 工作流全景

## 整体协作流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant BKD as BKD 看板
    participant N8N as n8n 门控
    participant Spec as Spec Agent<br/>(开发环境)
    participant Dev as Dev Agent<br/>(开发环境)
    participant Debug as 调试环境<br/>(K8s)

    Note over User,Debug: ═══ 有人阶段（合约驱动，有歧义就停）═══

    User->>BKD: 创建需求 Issue
    BKD->>N8N: 触发 /v2
    N8N->>BKD: 创建 [REQ-xx] Spec Issue<br/>follow-up prompt + update(working)

    BKD->>Spec: 启动 Spec Agent
    Spec->>Spec: 1. 分析需求，设计契约边界
    Spec->>Spec: 2. /opsx:propose 拆解需求
    Note right of Spec: openspec/changes/xxx/<br/>├── proposal.md<br/>├── specs/<br/>├── design.md<br/>└── tasks.md

    alt 需求有歧义
        Spec->>BKD: 在 Issue 里提问
        User->>BKD: 回答
        BKD->>Spec: follow-up
    end

    Spec->>Spec: 3. 基于 specs 写契约测试代码
    Spec->>Spec: 4. 基于 specs 写验收测试代码
    Spec->>Spec: 5. git commit（测试 LOCKED）
    Spec->>BKD: 6. 移 issue 到 review

    Note over User,Debug: ═══ 无人阶段（全自动，熔断兜底）═══

    BKD-->>N8N: webhook: issue.status.review (tag=spec)
    N8N->>N8N: 门控：检测到 spec 完成
    N8N->>BKD: 创建 [REQ-xx] Dev Issue<br/>follow-up prompt + update(working)

    BKD->>Dev: 启动 Dev Agent
    Dev->>Dev: 1. 读 openspec 产出物 + 测试代码
    Dev->>Dev: 2. 按 tasks.md 写业务代码
    Dev->>Dev: 3. 写单元测试
    Dev->>Dev: 4. git push

    Dev->>Debug: aissh: git pull + setup
    Dev->>Debug: aissh: L0 lint/compile
    Dev->>Debug: aissh: L1 单元测试
    Dev->>Debug: aissh: L2 契约测试（LOCKED）
    Dev->>Debug: aissh: L3 验收测试（LOCKED）
    Debug-->>Dev: 返回结果

    alt 测试失败
        Dev->>Dev: 修改业务代码（不改测试）
        Dev->>Debug: 重新验证（最多 3 轮）
    end

    Dev->>BKD: 移 issue 到 review

    BKD-->>N8N: webhook: issue.status.review (tag=dev)
    N8N->>N8N: 门控：检测到 dev 完成
    N8N->>BKD: 回调父 Issue "全部完成"
    N8N->>BKD: 父 Issue 设 review
    BKD-->>User: 通知用户 review
```

## 系统组件图

```mermaid
flowchart TB
    subgraph USER["用户"]
        U[创建需求 / Review / 回答问题]
    end

    subgraph N8N["n8n 门控层（2 个 webhook）"]
        W1["/v2<br/>入口：创建 Spec Issue"]
        W2["/bkd-events<br/>BKD 状态变更路由"]
        W2 --> ROUTE{按 tag 路由}
        ROUTE -->|spec| ACTION_SPEC[创建 Dev Issue]
        ROUTE -->|dev| ACTION_DEV[回调父 Issue + 设 review]
        OBS[(执行记录<br/>可观测性)]
    end

    subgraph BKD["BKD 执行引擎"]
        PARENT[父 Issue<br/>需求跟踪]
        SPEC_ISSUE[Spec Issue<br/>tag: spec]
        DEV_ISSUE[Dev Issue<br/>tag: dev]
        WEBHOOK[Webhook 通知<br/>issue.status.review<br/>session.completed<br/>session.failed]
        PROC[进程管理<br/>并发/超时]
    end

    subgraph DEV_ENV["开发环境（Coder Workspace）"]
        SPEC_AGENT[Spec Agent]
        DEV_AGENT[Dev Agent]
        OPSX[OpenSpec<br/>/opsx:propose]
        AISSH[aissh MCP]
    end

    subgraph DEBUG["调试环境（K8s vm-node04）"]
        L0[L0: lint/compile]
        L1[L1: 单元测试]
        L2[L2: 契约测试 LOCKED]
        L3[L3: 验收测试 LOCKED]
    end

    subgraph REPO["Git 仓库"]
        CODE[业务代码]
        SPECS[openspec/changes/xxx/]
        TESTS[测试代码 LOCKED]
    end

    %% 用户
    U --> PARENT
    PARENT -.-> U

    %% n8n 编排
    W1 -->|MCP| SPEC_ISSUE
    ACTION_SPEC -->|MCP| DEV_ISSUE
    ACTION_DEV -->|MCP| PARENT

    %% BKD webhook → n8n
    WEBHOOK -->|HTTP POST| W2

    %% BKD 执行
    SPEC_ISSUE --> SPEC_AGENT
    DEV_ISSUE --> DEV_AGENT

    %% Spec Agent
    SPEC_AGENT --> OPSX --> SPECS
    SPEC_AGENT --> TESTS
    SPEC_AGENT -->|移 review| WEBHOOK

    %% Dev Agent
    DEV_AGENT --> CODE
    DEV_AGENT --> AISSH
    AISSH --> L0 --> L1 --> L2 --> L3
    L2 -.->|读| TESTS
    L3 -.->|读| TESTS
    DEV_AGENT -->|移 review| WEBHOOK

    %% 代码传输
    CODE -->|push| REPO
    REPO -->|pull| DEBUG

    style N8N fill:#e1f5fe
    style BKD fill:#f3e5f5
    style DEV_ENV fill:#e8f5e9
    style DEBUG fill:#fff3e0
    style TESTS stroke:#f44336,stroke-width:3px
```

## n8n 工作流内部

```mermaid
flowchart LR
    subgraph ENTRY["/v2 入口（8 节点）"]
        E1[Webhook] --> E2[MCP Init]
        E2 --> E3[Set Ctx]
        E3 --> E4[Create Spec Issue]
        E4 --> E5[Extract ID]
        E5 --> E6[Follow-up Prompt]
        E6 --> E7[Update Working]
        E7 --> E8[Callback 父 Issue]
    end

    subgraph EVENTS["/bkd-events 路由（10 节点）"]
        V1[Webhook] --> V2[MCP Init]
        V2 --> V3[Set Ctx<br/>event/tags/issueId]
        V3 --> V4{Is Spec?}
        V4 -->|Yes| V5[Create Dev Issue]
        V5 --> V6[Extract ID]
        V6 --> V7[Follow-up Prompt]
        V7 --> V8[Update Working]
        V4 -->|No| V9{Is Dev?}
        V9 -->|Yes| V10[Callback 父 Issue<br/>+ Set Review]
    end

    E8 -.->|Spec Agent 完成<br/>BKD webhook 自动触发| V1
    V8 -.->|Dev Agent 完成<br/>BKD webhook 自动触发| V1
```

## 数据流

| 通道 | 用途 | 方向 |
|------|------|------|
| n8n webhook `/v2` | 入口触发 | 用户/BKD → n8n |
| BKD MCP (JSON-RPC) | n8n 创建/管理 Issue | n8n → BKD |
| BKD Webhook | Issue 状态变更通知 | BKD → n8n |
| OpenSpec `/opsx:propose` | 需求拆解 | Agent 内部 |
| git push/pull | 代码传输 | 开发环境 ↔ 调试环境 |
| aissh MCP | 远程控制调试环境 | 开发环境 → 调试环境 |
| BKD Issue 对话 | 可观测性 + 人机交互 | Agent ↔ 用户 |

## BKD Webhook 配置

```json
{
  "channel": "webhook",
  "url": "http://n8n.43.239.84.24.nip.io/webhook/bkd-events",
  "events": ["issue.status.review", "session.completed", "session.failed"],
  "isActive": true
}
```

Webhook payload 包含：event, issueId, projectId, title, tags, newStatus, 对话上下文。
n8n 通过 payload 中的 `tags` 判断是哪个阶段完成（spec/dev），路由到对应处理逻辑。
