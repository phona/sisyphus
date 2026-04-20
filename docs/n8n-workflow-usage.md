# n8n 工作流使用说明（v3）

权威架构：[architecture.md](./architecture.md)。当前实现：[workflow-current.md](./workflow-current.md)。本文只讲怎么导入、配凭证、触发、排查。

## 工作流文件

```
charts/n8n-workflows/
├── v3-entry.json      # /v2 入口 webhook（7 节点）：接需求 → 创建[REQ-xx] 需求分析 issue
└── v3-events.json     # /bkd-events 路由（55 节点）：接 BKD session.completed → 按 tags routeKey 路由
```

> 架构里提的 `shared/` `variants/` 目录目前没物化，所有 escalation / openspec-apply 都在 v3-events.json 里 inline。

## 路由原理

**title 完全不参与调度**。`Ctx` 节点从 `webhook.body.tags` 计算两个 key，所有 IF 节点都基于这两个 key 比较：

- **routeKey**（阶段路由）：`analyze` / `spec` / `dev` / `verify` / `bugfix` / `test-bugfix` / `accept`
- **resultKey**（结果路由）：`pass` / `fail` / `test-bug` / `spec-bug` / `unsupported` / `needs-clarify`

agent 完成阶段时通过 `update-issue(tags=[...])` 追加结果 tag（`result:pass` / `diagnosis:test-bug` 等），n8n 的 13 个 IF 节点全部基于此判定，不读 title。

完整路由表 + tag 协议 + title 撒谎也不影响调度的不变量见 [workflow-current.md](./workflow-current.md)。
agent 端要加哪些 tag 见 [prompts.md 结果 tag 协议](./prompts.md#结果-tag-协议重要)。

## 导入步骤

1. 登录 n8n Web 界面（http://n8n.43.239.84.24.nip.io）
2. **Workflows** → **Import from File** → 选 `charts/n8n-workflows/v3-entry.json`
3. 同样导入 `v3-events.json`
4. 两个 workflow **Activate**

> 重新导入会保留旧 webhook ID。如果改了 `webhookId` 字段，要先把旧版 deactivate 再激活新版，避免 path 冲突。

## 凭证配置

⚠️ **当前 v3 把 Coder Session Token 硬编码在 HTTP node header 里**（`GRvtsFrbNV-...`）。轮换 token 必须全文替换 v3-entry.json + v3-events.json + testcases/test-events-harness.sh。

**改进路径（待做）**：把 token 迁到 n8n Credentials → Generic Credential Type → Header Auth，HTTP node 引用 credential 而非 inline。

## 触发方式

### 入口（用户提需求）

POST `https://n8n.43.239.84.24.nip.io/webhook/v2`

```json
{
  "req_id": "REQ-10",
  "title": "用户头像上传",
  "description": "支持 png/jpg，限 2MB",
  "bkd_project_id": "workflowtest"
}
```

### BKD 事件回流（自动）

BKD 在 `Settings → Webhooks` 配置：

```json
{
  "url": "http://n8n.43.239.84.24.nip.io/webhook/bkd-events",
  "events": ["session.completed", "session.failed"]
}
```

**只订阅 `session.completed/failed`**，不订阅 `issue.status.review`（重复触发，见 `n8n-k3s-pitfalls.md` #12）。

BKD webhook payload 必含 `tags` 字段（数组），路由依赖此字段。

## 测试

`testcases/test-events-harness.sh`：覆盖 21 个路由 + gate + 熔断用例，**不调真 agent**（关 BKD webhook 防串扰）。

```bash
# 关 BKD webhook，跑全部用例
./testcases/test-events-harness.sh all

# 单个用例
./testcases/test-events-harness.sh case gate_pass

# 清理 TEST-* issue
./testcases/test-events-harness.sh clean

# 恢复 BKD webhook
./testcases/test-events-harness.sh webhook_on
```

## 熔断

Bug Fix（含 test-bugfix）累计 ≥ 3 → escalate。

判定走 `CB Query` (list-issues) → `CB Count`（统一 SSE→JSON 解析，按 tags `bugfix` ∪ `test-bugfix` 数 issue）→ `CB Tripped?`（数值比较）。

## 排查

### webhook 404

参见 [n8n-k3s-pitfalls.md #1](./n8n-k3s-pitfalls.md)：webhook 节点必须有 `webhookId` 字段。

### 路由没命中

1. n8n Web → Executions 查看本次 webhook 执行
2. 看 `Ctx` 节点输出的 `routeKey` 字段值
3. 对照下游 IF 节点的 `value2` 比较值

如果 `routeKey == 'unknown'`：webhook payload 里 `tags` 字段缺失或不含已知阶段标签。检查 BKD 端 issue 的 tags 是否按 [tag 规范](./workflow-current.md#issue-命名和-tag-规范)设置。

### gate 一直不放行

`All3?` gate 期望某些 tag 的 issue 状态为 `review`。打开 `Query` 节点的 SSE 响应，找 reqId 匹配的 issue，确认 tags 和 statusId。常见原因：

- analyze agent 没在 tags 加 `layer:foo`，All3? fallback 到 `dev-spec / accept-spec / contract-spec` 三路 expected
- 某 spec issue 还在 `working` 没移到 `review`
- `dev` tag 的 issue 已经存在（idempotency 防重复）

### 熔断意外触发

`CB Count` 数到的 bugfix 数包含历史所有 round。当前没按"本次 verify→bugfix 链路"分组，**任何 reqId 下累计 bugfix ≥ 3 都熔断**。这是已知简化，详见 [workflow-current.md](./workflow-current.md#已知问题)。

## 引用

- [架构设计（权威）](./architecture.md)
- [当前实现状态 + 已知问题](./workflow-current.md)
- [n8n on K3s 踩坑手册](./n8n-k3s-pitfalls.md)
- [agent prompt 大全](./prompts.md)
