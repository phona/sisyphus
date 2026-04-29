# Proposal: 修复 BKD tag 同步 race condition + admission 拒绝 UX

## 背景

1. `BKDRestClient.merge_tags_and_update` 和 `BKDMcpClient.merge_tags_and_update` 采用典型的读-改-写模式：先 `get_issue` 取当前 tags，本地合并 add/remove，再 `update_issue` 写回。当两个并发请求同时 get → 同时 update 时，后写的会覆盖先写的 tag。sisyphus 在多 stage 并行（verifier + fixer 同时更新 ctx/escalate tag）时有并发风险。

2. `start_analyze` 中的 admission gate 拒绝 REQ 时，只在 Postgres `ctx.escalated_reason` 中记录原因，BKD intent issue 上看不到任何说明。用户创建的 REQ 因为"并发已满"直接被 escalate，在 BKD 上只看到 review 状态的 issue，却不知为何被拒。

## 目标

1. 消除 `merge_tags_and_update` 的并发覆盖风险。
2. admission 拒绝时，在 BKD intent issue 上给用户可见的 tag 和消息反馈。

## 范围

- `bkd_rest.py` — `merge_tags_and_update` 加乐观锁重试
- `bkd_mcp.py` — 同步修改 MCP 客户端
- `start_analyze.py` — admission 拒绝时 BKD 同步
- `test_actions_start_analyze.py` — 测试更新

## 不做的

- 不改 BKD REST API（BKD 侧不支持原子 tag append/remove）
- 不改 admission 决策逻辑本身（cap / disk pressure 阈值不变）
- 不引入分布式锁或外部协调（乐观锁重试已足够）
