# REQ-cleanup-runner-zombie-1777170378: fix(admin): force_escalate must trigger runner Pod cleanup to prevent zombie Pods

## 问题

`POST /admin/req/{req_id}/escalate` (`admin.force_escalate`) 用 raw SQL UPDATE
把任意 state 的 REQ 推到 `escalated`，但**不调用 runner Pod cleanup**。

所有走状态机 transition 进 `ESCALATED` 的路径（engine.step 的 terminal-cleanup
钩子、escalate.py 内部 SESSION_FAILED 手 CAS）都会调用
`engine._cleanup_runner_on_terminal(req_id, ReqState.ESCALATED)`，删 Pod 留 PVC。

force_escalate 是这个清理网的唯一漏网：

```python
# admin.py 当前 force_escalate
await pool.execute(
    "UPDATE req_state SET state='escalated', "
    "context = context || $2::jsonb, updated_at = now() WHERE req_id = $1",
    req_id, '{"escalated_reason": "admin"}',
)
log.warning("admin.force_escalate", req_id=req_id, from_state=row.state.value)
return {"action": "force_escalated", ...}
# ↑ 没起 cleanup_runner_on_terminal task
```

后果（**zombie Pod**）：

1. force_escalate 后 state=`escalated`，但 runner Pod (`runner-<req-id>`) 仍在跑 `sleep infinity`
2. `runner_gc._active_req_ids` 把 escalated retention 期内的 REQ 当 active：

   ```python
   # runner_gc.py L52-58
   if state == "escalated" and not ignore_retention:
       updated_at = r["updated_at"]
       if updated_at and (now - updated_at) < retention:
           active.add(r["req_id"])
   ```

   active 集合内的 REQ 不进 `gc_orphans`，**整个保留期内 GC 一行都不动**
3. PVC 也带着，整段时间占着 8 GiB memory limit / 250m CPU request /
   `pvc_retain_on_escalate_days` × 多少 GiB workspace 磁盘
4. retention 过期后 `gc_orphans` 才扫到，调 `cleanup_runner(retain_pvc=False)`
   把 Pod + PVC 一锅端 —— 但中间这一整天，Pod 是真的活着

实证：vm-node04 5991Mi 总内存的小盘子，每个 runner pod request 512Mi，
"塞 2-3 个并发 runner"已经卡死调度。zombie Pod 占着别人调度不上。

## 方案

`admin.force_escalate` 在 SQL UPDATE 后**立即起 fire-and-forget cleanup task**，
mirror `admin.complete` 的实现：

```python
task = asyncio.create_task(
    engine._cleanup_runner_on_terminal(req_id, ReqState.ESCALATED)
)
_force_escalate_cleanup_tasks.add(task)
task.add_done_callback(_force_escalate_cleanup_tasks.discard)
```

`_cleanup_runner_on_terminal` 已根据 `terminal_state == ReqState.ESCALATED`
把 `retain_pvc` 设为 True —— 删 Pod、留 PVC 给人翻 workspace
debug，过期由 runner_gc 兜底清。跟所有走 transition 进 ESCALATED 的路径行为一致。

### 行为变化

```
之前：force_escalate → state=escalated → Pod 存活 retention 整段，PVC 留
之后：force_escalate → state=escalated + 立即 cleanup task → Pod 几秒内删，PVC 留
```

PVC 保留逻辑不变 —— 还是给人 follow-up 续 verifier issue 的窗口
（`(ESCALATED, VERIFY_FIX_NEEDED) → FIXER_RUNNING` 等三条 transition）。

### 实现要点

1. **fire-and-forget**：`asyncio.create_task` 起后立即返回 200，cleanup 失败
   有 `_cleanup_runner_on_terminal` 内部 try/except + warning log，runner_gc 周期兜底。
2. **task 引用**：用模块级 `_force_escalate_cleanup_tasks: set[asyncio.Task]`
   防 task 被 GC（done_callback 自清）；跟 `_complete_cleanup_tasks` 同模式但
   隔离作用域（便于测试 introspect 仅本 endpoint 起的 task）。
3. **state=escalated 的 noop 分支不起 cleanup**：第二次 force_escalate 看到
   `state == ReqState.ESCALATED` 直接返回 noop，不重复 schedule cleanup。
   原因：第一次 force_escalate 已经清过 Pod；重起一轮纯浪费 K8s API 调用 +
   多一行 warning 日志。
4. **不需要等 task 完成**：HTTP 调用方拿 200 表示"DB 已改 + cleanup 已排队"，
   不是"Pod 已删"。这跟 `admin.complete` 的契约一致。

### 与现有 endpoint 的对比

| endpoint | from_state | to_state | retain PVC | runner cleanup 触发 |
|---|---|---|---|---|
| force_escalate | * | escalated | yes | **本 REQ 之后：是**（之前漏） |
| complete | escalated | done | no | 是 |
| pause | * | (state 不动) | yes | pod-only delete（不算 terminal cleanup） |

## 取舍

- **为什么不在 runner_gc 改成 list Pod 而非 list PVC** —— 治标不治本：
  GC 周期默认 60s，cleanup miss 一直拖到下轮才补，期间 kubectl get pods
  看到一堆 escalated 的"沉睡"Pod。从根因（force_escalate 漏 schedule）修。
  另外 PVC 是 K8s 资源主键（Pod 可能被 K8s evict 重建，PVC 永远跟 REQ 一对一），
  GC 按 PVC sweep 是正确架构。
- **为什么 retain_pvc=True 不改成 False** —— 跟 transition 路径行为一致是关键
  原则：走 force_escalate 的 REQ 跟自动 escalate 的 REQ 应该有相同 PVC retention
  契约（人可能想 kubectl exec 进 PVC 看现场）。想要立即收 PVC 的 admin 应该用
  force_escalate（清 Pod 留 PVC）+ complete（清 PVC + state→done）两步。
- **为什么 noop 分支不补 schedule cleanup** —— "已经 escalated"语义就是"前面那
  次 force_escalate 已经把 Pod 清掉了"。如果有人手工 kubectl 重 apply 了 Pod
  又来调 force_escalate，那是 K8s 资源状态跟 sisyphus state 失同步，runner_gc
  PVC sweep 兜底（active set 含本 REQ → 不 sweep 当前 retention 内；过期才扫）。
  实际场景里这是边角，不值得正常路径多 schedule 一轮。
- **为什么不发 BKD tag 标 escalated_reason=admin-force** —— force_escalate 路径
  本就在 ctx 里写 `escalated_reason=admin`（已存在）；BKD intent issue tag 由
  正常 escalate action 维护，force_escalate 是绕过 escalate action 的纯 admin
  override，不该再去碰 BKD（避免污染 verifier 看板）。

## 影响面

- 改 `orchestrator/src/orchestrator/admin.py`：
  - `force_escalate` 函数体内加 cleanup task schedule（5 行新增）
  - 新增模块级 `_force_escalate_cleanup_tasks` set（防 task 被 GC）
  - 函数 docstring 增一段说明
- 测试：`orchestrator/tests/test_admin.py` 加 2 个 case：
  - `test_force_escalate_marks_escalated_and_triggers_cleanup`
  - `test_force_escalate_noop_when_already_escalated_no_cleanup`
- 不动 `engine.py` / `runner_gc.py` / `state.py` / migrations / BKD 集成层。
- 不动 `force_escalate` 的 HTTP 契约（输入 / 输出 schema 不变；调用方无感）。
