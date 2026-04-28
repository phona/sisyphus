# REQ-430 Proposal: runner pod + PVC 自动 GC — admin trigger + status

## 背景

`runner_gc.py` 已实现周期 GC（15 min 一次）和 Pod/PVC 分离 keep set（PR #169）。
但运维时无法：
1. 立即触发 GC（只能等下一个 15 min tick）
2. 查询上次 GC 跑了什么（只有 structlog，没有 REST 接口）

## 方案

在已有 admin API 基础上新增两个 endpoint：

### POST /admin/runner-gc
立即执行一次 `gc_once()`，返回结果。需 Bearer token。
无 K8s controller 时原样返回 `{"skipped": "..."}`.

### GET /admin/runner-gc/status
返回 `runner_gc._last_gc_result`：timer loop 和 admin trigger 都会更新这个
模块级变量。orchestrator 重启后清零（内存态，无需 DB，运维价值足够）。

### runner_gc._last_gc_result
`gc_once()` 每次成功（包括 "skipped"）都写 `_last_gc_result`，附上 `ran_at`（UTC ISO）。

## 风险

- 低：纯新增，不改现有 GC 逻辑
- 并发安全：asyncio 单线程，两端点共享同一 Python 进程内存，无竞态

## 范围

仅改 `orchestrator/src/orchestrator/`：
- `runner_gc.py`：+`_last_gc_result`, +`get_last_result()`
- `admin.py`：+2 endpoint
- 新增 openspec + 单测
