# n8n on K3s 踩坑手册

部署 n8n 2.x 到 K3s（SQLite 模式）时遇到的所有坑及解决方案。

## 1. Webhook 注册失败（404）

**现象**：工作流显示 `active: true`，日志也打印 `Activated workflow`，但调 `/webhook/xxx` 返回 404。

**根因**：通过 REST API 创建的工作流节点缺少 `webhookId` 字段。n8n UI 创建节点时会自动生成，但 API 不会。

**解决**：手动给 webhook 节点加 `webhookId`（UUID 格式）。

```json
{
  "type": "n8n-nodes-base.webhook",
  "webhookId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**注意**：Wait 节点内部也依赖 webhook 注册，同样需要 `webhookId`。但即使设了也可能注册失败（报 `No webhook path could be found`），建议 **避免使用 Wait 节点**。

## 2. 工作流执行崩溃（lastNode: None）

**现象**：webhook 返回 200 "Workflow was started"，但执行记录 status=error，lastNode=None，无任何节点执行。

**根因**：n8n 2.x 的 Task Runner 在 K3s 容器环境下不稳定。Runner 和 Broker 之间 WebSocket 认证 token 不同步，导致 `Unexpected end of JSON input` 或 `invalid or expired grant token`。

**表现形式**：
- Runner 启动后反复报 403，经过多次重试后才注册成功
- 注册成功后，某些表达式求值会导致 Runner 崩溃

**解决**：
1. 不要设置任何 `N8N_RUNNERS_*` 环境变量，让 n8n 使用默认配置
2. 避免使用以下功能（会触发 Runner 崩溃）：
   - Code / Function 节点
   - `JSON.stringify()` 在 HTTP Request 的 jsonBody 表达式中
3. 使用 Set + IF + HTTP Request 节点组合替代 Code 节点

## 3. JSON.stringify 导致 Runner 崩溃

**现象**：HTTP Request 节点的 `jsonBody` 中使用 `JSON.stringify()` 表达式时，执行直接崩溃（lastNode: None）。

**根因**：Task Runner 在处理含 `JSON.stringify()` 的表达式时序列化失败。

**解决**：用 n8n 模板表达式代替。

```
# 错误 ❌
={{ JSON.stringify({jsonrpc:"2.0",id:1,params:{name:$json.name}}) }}

# 正确 ✅
={"jsonrpc":"2.0","id":1,"params":{"name":"{{ $json.name }}"}}
```

## 4. SSE 响应解析困难

**现象**：BKD MCP 返回 SSE 格式（`event: message\ndata: {...}`），在 n8n 中难以提取嵌套的 JSON 数据。

**根因**：
- `fullResponse: true` + `responseFormat: text` 返回原始 SSE 文本
- SSE body 中的 JSON 多层转义（`\"id\": \"abc\"` 在文本中变成 `\\\"id\\\": \\\"abc\\\"`）
- 正则表达式在 n8n 表达式中提取转义 JSON 几乎不可能可靠实现

**解决**：
- 对于 MCP initialize：使用 `fullResponse: true` 提取 `$json.headers["mcp-session-id"]`（header 解析没问题）
- 对于 create-issue：**不要尝试提取 response 中的 issue ID**
- 改用 `create-issue(statusId: "working")` 跳过 ID 提取，agent 直接用 title 作为 prompt 开始执行

## 5. Set 节点数据污染

**现象**：Set 节点默认保留上游所有字段（`includeOtherFields: true`），MCP Init 返回的大量 SSE 文本数据被透传到下游节点，可能导致后续节点处理超大 payload 时崩溃。

**解决**：Set 节点设置 `includeOtherFields: false`，只保留需要的字段。

```json
{
  "type": "n8n-nodes-base.set",
  "parameters": {
    "includeOtherFields": false,
    "assignments": {
      "assignments": [
        {"name": "sid", "value": "={{ $json.headers[\"mcp-session-id\"] }}"}
      ]
    }
  }
}
```

## 6. WEBHOOK_URL 端口不匹配

**现象**：n8n 通过 traefik ingress（80 端口）暴露，但 `N8N_HOST` + `N8N_PORT` 生成的内部 webhook URL 带了 5678 端口，导致路由不匹配。

**解决**：设置 `WEBHOOK_URL` 环境变量，不带端口。

```yaml
- name: WEBHOOK_URL
  value: "http://n8n.example.com/"
```

注意：早期部署如果不设 WEBHOOK_URL，n8n 会用 `N8N_HOST:N8N_PORT` 生成 URL，端口不匹配会导致 webhook 虽然注册成功但无法匹配请求。

## 7. n8n 启动慢 + DB 超时

**现象**：n8n 容器启动后 2-3 分钟才能就绪，期间反复报 `Database connection timed out` 和 `Clock skew detected`。

**根因**：SQLite 在小资源容器 + local-path PVC 上 I/O 慢，加上 Task Runner 多次重试连接 Broker。

**解决**：
1. Deployment 设置 `strategy: Recreate`（避免新旧 pod 同时抢 SQLite 文件）
2. 设置 readinessProbe（等 DB 就绪后才接受流量）
3. 给容器至少 512Mi 内存

```yaml
readinessProbe:
  httpGet:
    path: /healthz
    port: 5678
  initialDelaySeconds: 10
  periodSeconds: 5
resources:
  requests:
    memory: "512Mi"
  limits:
    memory: "1Gi"
```

## 8. n8n 版本兼容性

| 版本 | 问题 |
|------|------|
| 2.16.1 (latest) | Task Runner 不稳定，但节点类型最全 |
| 2.12.2 | 同样的 Runner 问题 |
| 2.6.1 | Runner 问题较轻，但 `responseMode: onReceived` 不完全支持 |
| 1.94.1 | 无 Runner，但不支持 httpRequest v4.4、webhook onReceived |
| 1.70.3 | 不支持 webhook typeVersion 1 + onReceived |

**建议**：使用 latest（2.16.1+），接受 Runner 的不稳定性，通过避免 Code 节点和 JSON.stringify 来规避。

## 最佳实践总结

| 做法 | 说明 |
|------|------|
| 所有 webhook/wait 节点必须有 `webhookId` | API 创建不自动生成 |
| 不用 Code / Function 节点 | Task Runner 不稳定 |
| 不用 `JSON.stringify()` 在表达式中 | 用模板 `{{ $json.xxx }}` |
| Set 节点 `includeOtherFields: false` | 避免大数据透传 |
| 不用 Wait 节点 | 改用回调模式 |
| HTTP Request 提取 header 用 fullResponse | body 用 SSE 无法可靠解析 |
| `onError: continueRegularOutput` | BKD MCP 调用可能失败，不要阻塞流程 |
| 保持工作流节点数少（5-8 个） | 减少 Runner 出问题的概率 |
