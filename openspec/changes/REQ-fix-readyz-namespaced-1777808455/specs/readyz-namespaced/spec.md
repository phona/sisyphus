# readyz-namespaced

## ADDED Requirements

### Requirement: /readyz K8s 探活 SHALL 走 namespaced pod list 而非 list_namespace

orchestrator 的 `GET /readyz` 端点在做 K8s 连通性探活时 SHALL 调用
`controller.core_v1.list_namespaced_pod`，target namespace MUST 取
`controller.namespace`（即 `settings.runner_namespace`，默认 `sisyphus-runners`），
并 MUST 带 `limit=1` 避免拉全量 pod 列表。该调用 MUST 在 `asyncio.to_thread`
里跑，并 MUST 传 `_request_timeout=2`（秒），让单次探活最长占用 2 秒。

实现 MUST NOT 调 `controller.core_v1.list_namespace` 或任何其它需要 cluster-wide
RBAC 的 API；helm chart 给 orch ServiceAccount 装的是 namespace-scoped Role
（作用域 `sisyphus-runners`），任何 cluster-wide 调用都会被 K8s API server
拒绝（403），导致 readyz 无条件 503。

#### Scenario: RZN-S1 happy path readyz 200 with limit=1 namespaced call

- **GIVEN** orchestrator 启动完毕，K8s controller 已初始化，DB / BKD 探活均通过
- **AND** `controller.namespace == "sisyphus-runners"`
- **WHEN** 客户端发 `GET /readyz`
- **THEN** handler MUST 调 `controller.core_v1.list_namespaced_pod` 恰好一次
- **AND** 调用位置参数 MUST 是 `"sisyphus-runners"`
- **AND** 调用 kwargs MUST 包含 `limit=1` 和 `_request_timeout=2`
- **AND** handler MUST NOT 调 `controller.core_v1.list_namespace`
- **AND** 响应 MUST 是 HTTP 200，body `{"status": "ok"}`

#### Scenario: RZN-S2 K8s API 异常时 readyz 503 且 failed 含 k8s

- **GIVEN** K8s controller 已初始化，但 `list_namespaced_pod` 抛非 RuntimeError
  异常（例如 `ApiException(403)`、网络超时）
- **AND** DB / BKD 探活均通过
- **WHEN** 客户端发 `GET /readyz`
- **THEN** 响应 MUST 是 HTTP 503
- **AND** body MUST 含 `{"status": "not_ready", "failed": [...]}`
- **AND** `failed` 列表 MUST 含 `"k8s"`
- **AND** `failed` 列表 MUST NOT 含 `"db"` 或 `"bkd"`

#### Scenario: RZN-S3 controller 未初始化（dev/test）跳过 K8s 探活不算失败

- **GIVEN** `k8s_runner.get_controller()` 抛 `RuntimeError("not init")`（dev/test
  模式 orch 起来时不一定接 K8s）
- **AND** DB / BKD 探活均通过
- **WHEN** 客户端发 `GET /readyz`
- **THEN** handler MUST 把 `RuntimeError` 视为 skip 而非 fail
- **AND** 响应 MUST 是 HTTP 200，body `{"status": "ok"}`
- **AND** `failed` 列表 MUST NOT 含 `"k8s"`
