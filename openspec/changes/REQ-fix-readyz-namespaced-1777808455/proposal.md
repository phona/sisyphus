# Proposal: /readyz K8s 探活改用 namespaced pod list

## Problem

`orchestrator/src/orchestrator/main.py` 的 `/readyz` 走
`controller.core_v1.list_namespace(_request_timeout=2)` 探 K8s 连通性。
`list_namespace` 是 cluster-wide API，但 helm chart 只给 orch ServiceAccount
装了 namespace-scoped Role（作用域 `sisyphus-runners`），没 ClusterRole。

结果：

- `/readyz` 永远返 503 `{"failed": ["k8s"]}`
- k8s readinessProbe 失败 → service 拒接 traffic
- orch 实际行为不受影响（livenessProbe 走 `/livez`，runner pod 调度正常）
- 但 readyz 误报会污染监控告警，并阻挡未来基于 readiness 的路由策略

PR #319 拆 livez/readyz 时引入此 bug。Issue #344 记录全细节。

## Solution

把 K8s 探活从 `list_namespace` 切到 `list_namespaced_pod`，目标 namespace 取
`controller.namespace`（即 `settings.runner_namespace`，默认 `sisyphus-runners`），
带 `limit=1` 让 API server 只返一条记录开销最低。

`controller.namespace` 已经是 orch 实际工作的 namespace（kshall_runner 用它跑
所有 pod / pvc 操作），探它不需要任何额外 RBAC —— chart 现有的 namespace-scoped
Role 已包含 `pods: list`。

## Why not 方案 B（补 ClusterRole）

issue #344 提的两选一里 B 是"补 ClusterRole 给 orch 拿 cluster-wide 权限"。
拒绝理由：

- orch 业务上**不需要**读 namespace 列表 —— 只跑 sisyphus-runners 内的 pod
- 给 SA 多余权限是 anti-pattern，违反最小权限
- A 方案 0 chart 改动，回滚摩擦最小

## Scope

- `orchestrator/src/orchestrator/main.py` — `/readyz` K8s 探活实现
- `orchestrator/tests/test_healthz.py` — 三处 mock 从 `list_namespace` 切到
  `list_namespaced_pod`，并新增正向场景 assert 调用参数

## Out of scope

- helm chart RBAC（不动，A 方案不需要）
- `/livez` / `/healthz`（不动）
- DB / BKD 探活逻辑（不动）
