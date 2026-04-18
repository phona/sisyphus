# 当前工作流状态

## 系统组件

```
n8n (vm-node04 K3s)
  ├── /v2           — 入口 webhook (7 节点)
  └── /bkd-events   — BKD 事件路由 (42 节点)

BKD (Coder Workspace)
  ├── webhook → n8n /bkd-events (session.completed/failed)
  └── projects/workflowtest (ubox-crosser 仓库)

调试环境 (vm-node04)
  └── aissh MCP 远程控制
```

## 完整流程

```
用户创建需求 → /v2 触发

阶段一：需求分析（串行）
  n8n 创建 [REQ-xx] 需求分析 issue
  Agent: /opsx:propose → openspec artifacts + contract.spec.yaml
  完成 → BKD webhook → n8n

阶段二：三路并行 Spec
  n8n 同时创建 3 个 issue:
    [REQ-xx] 开发Spec       → dev.spec.md
    [REQ-xx] 契约测试Spec    → contract_test.* (LOCKED)
    [REQ-xx] 验收测试Spec    → acceptance_test.* (LOCKED)
  每个完成 → BKD webhook → n8n 检查 3 个都完成？
    是 → 放行
    否 → 等待

阶段三：开发
  n8n 创建 [REQ-xx] 开发 issue
  Agent: TDD 小步快跑，用 aissh 在调试环境验证
  完成 → BKD webhook → n8n

阶段四：测试验证
  n8n 创建 [REQ-xx] 测试验证 issue
  独立 Agent: 用 aissh 在调试环境跑 L0-L3
  结果:
    PASS → 创建验收 issue
    FAIL → 创建 Bug Fix issue → 修完 → 再创建测试验证 → battle 循环

阶段五：验收
  n8n 创建 [REQ-xx] 验收 issue
  独立 Agent: 部署 + 跑 acceptance_test
  结果:
    PASS → Done
    FAIL → 创建 Bug Fix → battle
```

## n8n /bkd-events 路由逻辑

```
收到 BKD webhook (session.completed)
  → 提取 title
  → IF 链（最具体优先）:

  "验收" → Done (回调父 issue)
  "Bug Fix" → 创建 测试验证 issue
  "测试验证" → 检查 PASS/FAIL
    PASS → 创建 验收 issue
    FAIL → 创建 Bug Fix issue
  "开发" → 创建 测试验证 issue
  "Spec" → 查 BKD 确认 3 个都完成
    都完成 → 创建 开发 issue
    未完成 → 等待
  "需求分析" → 同时创建 3 个 Spec issue
```

## Issue 命名和 Tag 规范

```
[REQ-xx] 需求分析      tag: analyze, REQ-xx
[REQ-xx] 开发Spec      tag: dev-spec, REQ-xx
[REQ-xx] 契约测试Spec   tag: contract-spec, REQ-xx
[REQ-xx] 验收测试Spec   tag: accept-spec, REQ-xx
[REQ-xx] 开发           tag: dev, REQ-xx
[REQ-xx] 测试验证       tag: verify, REQ-xx
[REQ-xx] Bug Fix        tag: bugfix, REQ-xx
[REQ-xx] 验收           tag: accept, REQ-xx
```

## 已知问题

1. **并行 Spec 可能创建重复 Dev** — gate 检查用 list-issues SSE 文本 contains，不够精确
2. **测试验证 PASS/FAIL 判断依赖 title** — agent 需要在移 review 前修改 title 包含 PASS/FAIL
3. **父 issue 状态未自动更新** — BKD webhook payload 不含 parentId
4. **aissh 指令未强制** — prompt 写了用 aissh，但 agent 可能自己在 worktree 跑
