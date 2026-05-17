# REQ-np-demo4-1779013770: NodePort + make endpoint + no-wait demo

## 问题

需要在零业务面积下端到端 smoke 一次 sisyphus 完整管道，同时向 testkit/httpx
提供三项面向 K8s 接受测试的能力：

1. **BC1 – NodePort URL 构造**：runner 在集群内通过 NodePort 访问服务时需要
   `http://<nodeIP>:<nodePort>` 格式的 URL；目前无标准辅助函数，消费方各写一行
   `fmt.Sprintf`，不利于统一。
2. **BC2 – make endpoint 动态发现**：接受测试环境 URL 不固定（NodePort 随部署变化），
   业务仓已有 `make endpoint` Makefile target 输出当前 URL；testkit 应能调用该
   target 并直接建立 HTTPClient，避免消费方手写 `exec.Command` + `TrimSpace`。
3. **BC3 – no-wait POST**：callboard `/api/v1/callboard/device/bind_code` 等异步
   触发类接口只要求服务器收到请求即返回，客户端不应阻塞等待响应体；testkit 缺少
   fire-and-forget 语义的 POST 方法，消费方只能用完整 `Post` 再丢弃响应体。

## 方案

### testkit/httpx 新增三项能力

| 文件 | 新增内容 |
|---|---|
| `testkit/httpx/nodeport.go` | `NodePortURL(nodeIP string, nodePort int) string` |
| `testkit/httpx/make_endpoint.go` | `NewServiceClientFromMake(tb, makeTarget, dir string, opts ...Option) *HTTPClient` |
| `testkit/httpx/client.go` | `(c *HTTPClient) PostNoWait(tb, path string, body any, headers ...map[string]string) int` |

### orchestrator/_pipeline_marker.py

追加 `NP_DEMO4_REQ: str = "REQ-np-demo4-1779013770"` 与既有三条常量共存，
无生产路径 import，无副作用。

## 取舍

- **为什么 NodePortURL 不支持 HTTPS**：接受环境内网无 TLS，硬编码 `http://` 降低
  消费方出错面积；需要 HTTPS 的场景可直接 `NewServiceClient`。
- **为什么 NewServiceClientFromMake 接受 dir 参数**：`make` 需要在含 Makefile 的
  目录执行；`dir=""` 默认为 `"."`（调用方当前目录），测试可传 `t.TempDir()`。
- **为什么 PostNoWait 返回 int（status code）而非 void**：调用方通常需要确认服务
  已收到请求（2xx），返回状态码比完全静默更有用；不读响应体即满足"不等待"语义。
- **为什么不新建 httpx 子包**：三个新增都是 HTTPClient / URL 工具，与现有 httpx
  内聚，无需新包。
