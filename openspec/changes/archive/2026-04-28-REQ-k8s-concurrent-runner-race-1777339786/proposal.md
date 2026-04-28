# REQ-k8s-concurrent-runner-race-1777339786 Proposal

## 问题

两条 dogfood REQ 间隔 < 1s 同时进入 `start_analyze`，其中一条在 `_wait_pod_ready`
阶段立即 escalate。根因：`RunnerController` 共享单一 `CoreV1Api` 实例，两条 REQ 各自
通过 `asyncio.to_thread` 并发调用 `read_namespaced_pod_status`，kubernetes-python
ApiClient 内部 state 被并发线程串，随机走入 `ws_client.websocket_call`，把合规 HTTP 200
JSON 响应当 WebSocket 握手失败，抛 `ApiException(status=0)`。

## 修复方案

在 `RunnerController` 上加 `asyncio.Lock`，通过私有 `_k8s()` 辅助方法，把**所有** 15
个 `asyncio.to_thread(self.core_v1.<method>, ...)` 调用串行化。串行化对吞吐量无影响：
K8s API 调用延迟实测 < 1s，串行后最坏情况多等 1s，不影响整体派单速度。

## 不改的部分

- kubernetes_asyncio 迁移（#1 方案）：侵入全部 K8s 调用点，本次 ~5 行 lock 已够
- ensure_runner 重试次数 / watchdog 异常处理逻辑
- BKD POST 派单流程
