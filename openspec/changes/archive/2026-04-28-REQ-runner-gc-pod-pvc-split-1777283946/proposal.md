# REQ-runner-gc-pod-pvc-split-1777283946: refactor(runner_gc): split Pod vs PVC retention so escalated Pods are reclaimed immediately while PVCs honor the human-debug retention window

## 问题

`runner_gc` 当前用**一个** keep set 同时管 Pod 和 PVC：

```python
# runner_gc.py 当前
async def _active_req_ids(*, ignore_retention: bool = False) -> set[str]:
    ...
    if state == "escalated" and not ignore_retention:
        if updated_at and (now - updated_at) < retention:
            active.add(r["req_id"])    # ← Pod 跟 PVC 一起留
    return active

async def gc_once() -> dict:
    ...
    active = await _active_req_ids(ignore_retention=disk_pressure)
    cleaned = await rc.gc_orphans(active)   # 列 PVC，扫到的同时删 Pod + PVC
```

- `gc_orphans(active)` 只迭代 PVC（label `sisyphus/role=workspace`）；ESCALATED 在
  retention 内的 REQ 整段时间被当 active，PVC 在 keep set 里，**Pod 跟 PVC 一锅
  端不动**
- 进 ESCALATED 时引擎跑的 fire-and-forget `_cleanup_runner_on_terminal` 调
  `cleanup_runner(retain_pvc=True)`，正常情况下 Pod 几秒内删
- **如果那条 task 失败**（K8s API 抖动、orchestrator pod 重启把 task 吃了、
  asyncio loop 在 task 跑完前关），Pod **整段 retention 都活着**，每个
  pod request 512 Mi、limit 8 Gi —— 在 vm-node04（5991 Mi 总内存）小盘子上
  直接吃掉别 REQ 的调度容量

REQ-cleanup-runner-zombie-1777170378 修了 `force_escalate` 漏 schedule cleanup
的问题；本 REQ 接力把 GC 兜底也补成"Pod 立即清"——任何路径漏的 Pod 一个
GC tick 内补上。

实证：vm-node04 5991Mi 总内存的小盘子，每个 runner pod request 512Mi，
"塞 2-3 个并发 runner"已经卡死调度（k8s_runner.py L266-271 注释）。一个
zombie ESCALATED Pod 整天占着，对调度容量来说就是少一个 in-flight REQ 的
slot。

## 方案

把 `runner_gc` 的 keep set 拆成两个：

| | Pod keep set | PVC keep set |
|---|---|---|
| non-terminal | 留 | 留 |
| `done` | **清**（立即） | **清**（立即） |
| `escalated`（retention 内） | **清**（立即） ← 本次新增 | 留 |
| `escalated`（retention 过） | **清** | **清** |
| disk pressure | 同上 | escalated 也清（紧急疏散） |

对应 `RunnerController` 把单一 `gc_orphans(keep)` 拆成两个职责单一方法：

- `gc_orphan_pods(pod_keep) -> list[str]`：列 Pod (`sisyphus/role=runner`)，
  删 keep 之外的 **Pod，不动 PVC**
- `gc_orphan_pvcs(pvc_keep) -> list[str]`：列 PVC (`sisyphus/role=workspace`)，
  删 keep 之外的 **PVC，不动 Pod**（Pod GC 已独立扫；PVC 还有 Pod 依附时
  K8s 把 PVC 标 Terminating 等 Pod 走，下轮 GC 重扫即生效）

`gc_once()` 调两个新方法，返 dict 加 `cleaned_pods` + `cleaned_pvcs`。

### 行为变化

```
之前：进 ESCALATED → fire-and-forget cleanup 删 Pod
      若 cleanup task 失败 → Pod 整段 retention（默认 1d）当 zombie
      retention 过期 → gc_orphans 一锅端 Pod + PVC

之后：进 ESCALATED → fire-and-forget cleanup 删 Pod
      若 cleanup task 失败 → 下个 GC tick（默认 15min）gc_orphan_pods 兜底
      PVC 仍按 retention 留给人 debug
      retention 过期 → gc_orphan_pvcs 单独扫 PVC
```

PVC retention 语义不变 —— 还是给人 follow-up 续 verifier issue 的窗口、
跑 admin force_escalate 后 kubectl exec 进 PVC 看现场。

### 实现要点

1. **不删 `gc_orphans` 仅替换调用方**：`gc_orphans` 是 controller 唯一对外接口，
   把它改成 `gc_orphan_pods` + `gc_orphan_pvcs` 两个方法（CLAUDE.md "避免
   backwards-compatibility hacks"）。runner_gc / 测试同步更新。
2. **Pod GC 按 Pod label 列**（不是 PVC label 推 Pod 名）：覆盖 PVC 已被删但
   Pod 残留的边角（之前 gc_orphans 用 PVC iterate，PVC 没了找不到 Pod）。
3. **PVC GC 不级联删 Pod**：拆成正交职责。Pod 还在时 PVC 删请求由 K8s 接受 +
   标 Terminating 等 Pod 走 —— 下一轮 Pod GC 删完后 PVC 自动收。
4. **`cleanup_runner` / `_cleanup_runner_on_terminal` 不动**：transition 路径
   即时清理 + admin endpoint 的 fire-and-forget cleanup 仍走老路。GC 是兜底层。
5. **disk-check / RBAC 降级路径不动**：`_DISK_CHECK_DISABLED` flag 行为同前；
   ORCHN-S4..S8 contract 全过。

### 与 runner-cleanup 调用图

```
transition → ESCALATED
   ↓ fire-and-forget
engine._cleanup_runner_on_terminal(req_id, ESCALATED)
   ↓
RunnerController.cleanup_runner(req_id, retain_pvc=True)   # 删 Pod 留 PVC
   ↓ task 失败时漏网
[zombie Pod 直到 retention 过期]                            # ← 之前

[本 REQ 兜底]:
runner_gc.gc_once() (每 15 min)
   ↓
RunnerController.gc_orphan_pods(pod_keep)                  # 删 zombie Pod
RunnerController.gc_orphan_pvcs(pvc_keep)                  # 仍按 retention 留 PVC
```

## 取舍

- **为什么不缩短 `runner_gc_interval_sec`** —— 间隔 15min 是 K8s API 配额
  权衡，缩短到 1min 增 15× API 调用对治标。本 REQ 改的是"GC tick 真扫到时
  扫不扫 ESCALATED Pod"——根因，不是频率。间隔保留作 ops 旋钮（settings）。
- **为什么 `done` 也走 Pod GC（看似多余）** —— transition / admin.complete 已
  fire-and-forget 清。GC 是兜底：fire-and-forget 失败时这里捞回来。两条路径
  互补，跟 cleanup_runner 的 404 幂等性合作不冲突。
- **为什么不在 GC 里继续按 PVC iterate 推断 Pod 名** —— 边角（PVC 删了 Pod
  残留）覆盖不到；按 Pod label 列直接、对称。两个新方法是正交职责，跟资源
  管理的语义对得上（Pod 内存、PVC 磁盘）。
- **为什么 PVC GC 不也清 Pod** —— 把"PVC 用满删 Pod"耦合进 PVC sweep 让 GC
  逻辑两栖，更难推。Pod GC 独立职责，由 keep set（Pod-only）决定；PVC GC
  独立职责。K8s 自身的 Pod-PVC 依赖处理（Terminating 等 Pod 走）已经够用。
- **为什么不也写一个 Pod-only 的 cleanup endpoint** —— GC 是周期性兜底层，
  对单个 REQ 即刻 Pod 释放有 `/admin/req/{req_id}/runner-pause`（删 Pod 留
  PVC，已经存在）—— 跟本 REQ 的 GC 拆分契约语义一致。

## 影响面

- 改 `orchestrator/src/orchestrator/runner_gc.py`：
  - `_active_req_ids` 拆成 `_pod_keep_req_ids` + `_pvc_keep_req_ids`
  - `gc_once` 调两个新方法，返 dict 加 `cleaned_pods` / `cleaned_pvcs` /
    `pod_kept` / `pvc_kept` 字段（保留 `disk_pressure`）
  - `run_loop` 日志字段调整为 pods/pvcs
- 改 `orchestrator/src/orchestrator/k8s_runner.py`：
  - 删 `gc_orphans`，新增 `gc_orphan_pods` + `gc_orphan_pvcs`
- 测试：
  - `orchestrator/tests/test_runner_gc.py` —— 现有 case 改 assert 两个 keep set；
    加 `test_pod_keep_excludes_escalated_within_retention`
  - `orchestrator/tests/test_k8s_runner.py` —— 替 `test_gc_orphans_removes_not_in_keep_set`
    成 pods + pvcs 两个独立测试
  - `orchestrator/tests/test_contract_orch_noise_cleanup.py` —— `_FakeController`
    替 `gc_orphans` 成 `gc_orphan_pods` + `gc_orphan_pvcs`（5 处）
- 不动 `engine.py` / `admin.py` / `state.py` / migrations / BKD 集成层 / settings。
- 不动 `cleanup_runner` / `_cleanup_runner_on_terminal` 的契约（transition / admin
  调用方无感）。
