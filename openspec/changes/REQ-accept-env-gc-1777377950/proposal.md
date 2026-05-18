# REQ-accept-env-gc-1777377950: feat(accept_env_gc): implement full GC subsystem replacing skeleton

## 问题

`accept_env_gc.py` 当前是 file-skeleton placeholder，`gc_once()` / `run_loop()` 直接抛
`NotImplementedError`。`main.py` 未启动该服务。

accept 阶段通过 `make accept-env-up` 在 K8s cluster 上创建临时 namespace（命名格式
`accept-{req_id.lower()}`），用于部署 lab 环境跑 acceptance test。
`make accept-env-down` 在 create_accept 脚本末尾和 teardown_accept_env action 中
各跑一次（幂等、best-effort），但任一路径失败都可能导致 namespace 泄漏：

- `create_accept` 脚本里的 env-down 失败：accept 整体 pass/fail 后脚本退出，env-down
  在循环末尾用 `|| true` 忽略错误
- `teardown_accept_env` 里的 env-down 失败：只 warning 不阻塞状态机
- orchestrator restart：可能吃掉正在跑的 teardown fire-and-forget task
- K8s API blip：delete 请求丢了，namespace 留在 cluster 里

泄漏的 accept namespace 持续占用 K8s 资源（deployment / service / pod / configmap 等），
在小盘子 K3s 上直接吃掉调度容量。

## 方案

实现完整的 `accept_env_gc` 子系统，跟 `runner_gc` 同模式：

| 维度 | runner_gc | accept_env_gc |
|---|---|---|
| 扫描对象 | Pod + PVC（`sisyphus-runners` ns） | Namespace（`accept-req-*`，cluster-wide） |
| 保留依据 | req_state 非终态 / escalated retention | req_state 非终态 / escalated retention |
| 清理动作 | delete pod / delete pvc | delete namespace（级联删内部全部资源） |
| 周期 | 15 min | 15 min（独立配置） |
| 终态 | done 立即清 / escalated 留 retention | done 立即清 / escalated 留 retention |

happy path（done）立即清，无 debug 价值；escalated 路径留
`pvc_retain_on_escalate_days` 窗口供操作员 `kubectl describe/logs` 失败的 accept
lab 现场（issue #572）。

### 实现要点

1. **accept_env_gc.py 完整实现**：
   - `gc_once()`：从 req_state 读所有行，建非终态 keep set；调用 controller
     `list_accept_env_namespaces()` 获取 `accept-req-*` namespace 列表；不在 keep set
     中的删（404 视为已清理）。返 dict 含 `cleaned_namespaces` / `kept_namespaces`
     / `cleaned_count` / `kept_count` / `ran_at`。
   - `run_loop()`：周期性循环，跟 runner_gc 同模式（sleep → gc_once → log）。
   - `get_last_result()`：返回上次 GC 结果（供 admin status endpoint）。

2. **RunnerController 新增 K8s API 方法**：
   - `list_accept_env_namespaces() -> list[str]`：优先按 label
     `sisyphus/role=accept-env` 过滤，fallback 到 `accept-req-*` prefix。
   - `delete_namespace(name) -> None`：幂等删 namespace，404 = no-op。

3. **配置**：`config.py` 新增 `accept_env_gc_interval_sec: int = 900`（0 = 关闭）。

4. **启动 wiring**：`main.py` startup 在 runner_gc 之后启动 `accept_env_gc.run_loop()`。

5. **Admin endpoint**：
   - `POST /admin/accept-env-gc` —— 手动触发一次 GC pass
   - `GET /admin/accept-env-gc/status` —— 上次结果（免认证）

6. **测试**：删除 skeleton 占位测试，新建 `test_accept_env_gc.py`（9 case）：
   - 非终态 REQ 保留 namespace
   - done REQ 清理 namespace
   - escalated REQ 清理 namespace（无 retention）
   - orphan namespace（req_state 找不到）清理
   - 无 namespace 时空扫
   - 无 controller 时跳过
   - _last_result 更新
   - skipped 也更新 _last_result
   - 404 视为已清理

### 与现有系统的关系

```
create_accept (runner pod 内)
   ↓ make accept-env-up 创建 accept-REQ-xxx namespace
   ↓ make accept-smoke 跑 acceptance
   ↓ make accept-env-down (best-effort, || true) 尝试清理

teardown_accept_env (sisyphus action)
   ↓ 按 accept_result 分流
   ↓ make accept-env-down (best-effort, 失败只 warning) 再试一次

[本 REQ 兜底]:
accept_env_gc.gc_once() (每 15 min)
   ↓ 列所有 accept-req-* namespace
   ↓ 对应 REQ 终态 / orphan → delete namespace
```

accept_env_gc 跟 teardown_accept_env 互补：teardown 在状态机 transition 时即时清理，
GC 在周期性 tick 中兜底任何漏网之鱼（K8s API 失败、orchestrator restart 吞 task、
人为手动创建测试 namespace 后忘删等）。

## 取舍

- **为什么不把 accept env GC 合进 runner_gc** —— runner_gc 扫的是
  `sisyphus-runners` namespace 内的 Pod/PVC，accept env namespace 是 cluster-wide
  的，扫描维度不同。合进去让 runner_gc 职责不纯（同时要管 ns-level 资源）。独立模块
  可独立开关（`accept_env_gc_interval_sec=0` 关闭）。
- **为什么 escalated 留 retention** —— 跟 runner_gc PVC 同窗口。escalated 多半是
  accept 失败导致的，namespace 里的 pod logs / events / configmap 是第一手现场，
  没了就只能事后猜。done 路径仍立即清，无 debug 价值。issue #572 的根因之一就是
  之前 escalated 也立即清，操作员来不及看现场。
- **为什么不通过 label 严格过滤** —— `list_accept_env_namespaces` 优先按
  `sisyphus/role=accept-env` label 过滤，fallback 到 `accept-req-*` prefix，兼容早期
  没打 label 的 namespace。
- **为什么 namespace 级删除而不是逐个资源删** —n namespace delete 是 K8s 级联操作，
  一次 API 调用删掉内部全部资源（deployment / service / pod / pvc / configmap 等），
  比逐个资源遍历更高效、更不容易漏。

## 影响面

- 改 `orchestrator/src/orchestrator/accept_env_gc.py`：替换 skeleton 为完整实现
- 改 `orchestrator/src/orchestrator/k8s_runner.py`：新增
  `list_accept_env_namespaces` + `delete_namespace`
- 改 `orchestrator/src/orchestrator/config.py`：新增 `accept_env_gc_interval_sec`
- 改 `orchestrator/src/orchestrator/main.py`：startup 启动 accept_env_gc loop
- 改 `orchestrator/src/orchestrator/admin.py`：新增 accept-env-gc 运维 endpoint
- 测试：删除 `test_accept_env_gc_skeleton.py` +
  `test_contract_accept_env_gc_skeleton.py`，新建 `test_accept_env_gc.py`
- 不动 `engine.py` / `state.py` / `actions/` / migrations / BKD 集成层。
