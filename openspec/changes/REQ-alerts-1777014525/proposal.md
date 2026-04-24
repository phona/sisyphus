# REQ-alerts-1777014525: feat(alerts): 三类静默失败可观测 + 两阶段 watchdog + PVC GC

## 问题

生产环境中存在三类静默失败，均不触发告警、难以发现：

1. **PVC 慢速满盘**：runner PVC 持续写入，无监控，磁盘用尽后 pod 崩溃，escalate reason 无意义（`issue-updated`）
2. **runner pod 启动失败**：`ensure_runner` 超时后 escalate reason 为 `issue-updated`，完全无法区分是 pod 调度失败还是代码问题
3. **fixer 死循环**：verifier→fixer→verifier 循环 >3 轮无 net progress，无自动止损，消耗大量 token

## 根因

- `alerts` 表不存在，orchestrator 没有统一告警写入路径
- `escalate.py` 直接用 `body.event` 作 reason，无 ctx 优先级
- `apply_verify_pass` 的 `ensure_runner` 超时不记录 K8s 诊断
- watchdog 只有 30min 硬 escalate，无早期 5min warn
- `runner_gc` 只 GC runner pod，不管 PVC
- `invoke_verifier_after_fix` 没有轮次上限

## 方案

### A. alerts 表 + store 层

新建 `alerts` 表（migration 0008），severity CHECK('info','warn','critical')。
`store/alerts.py` 提供 `insert_alert` / `mark_sent_to_tg`；`alerts/__init__.py` 暴露 `insert()`（自动取 pool）。

### B. Telegram Bot push

`alerts/tg.py` 实现 `send_critical(text)`，读 `settings.tg_bot_token` / `settings.tg_chat_id`，无配置时静默跳过。

### C. escalate reason 精化

`escalate.py`：`ctx.escalated_reason` 优先级高于 `body.event` 名。所有 escalate 写 alerts 表 + 推 TG。

### D. K8s pod 诊断

`k8s_runner.K8sRunnerController` 新增 `_diagnose_pod(pod_name)` 方法（读 K8s events API，识别 ImagePullBackOff / PVC pending / resource insufficient）；新增 `delete_pvc(req_id)` 方法。

### E. runner 超时诊断上下文

`apply_verify_pass` 捕获 `ensure_runner` 的 `TimeoutError`，调 `_diagnose_pod`，写 `ctx.escalated_reason="runner-pod-not-ready"` + `escalated_hint=<诊断结果>`，再 re-raise 走原有 SESSION_FAILED 路径。

### F. watchdog 两阶段

SQL 阈值降到 `_WARN_THRESHOLD_SEC=300s`（5min）抓取 stuck rows，5–30min 写 `alert(severity=warn)` + `ctx.warned_at_5min=True`；≥30min 走原 escalate path + `ctx.escalated_reason="watchdog-stuck-30min"`。

### G. fixer 循环检测

`invoke_verifier_after_fix` 在 append 当前轮次到 `ctx.verifier_history` 后检查 `len(history) > 3`，超过则写 `escalated_reason="fixer-loop-3rounds"` + 发 `VERIFY_ESCALATE` event，终止循环。

### PVC GC

`runner_gc.gc_pvcs()` 独立于 `gc_once()`：done → 立即删，escalated → 保留 24h 后删，disk>80% → 强清非 active PVC。`run_loop()` 每轮也调 `gc_pvcs()`。

## 取舍

- **TG 失败不阻断**：网络抖动不影响状态机，告警尽力而为
- **fixer 阈值=3轮**：保守，允许真实多轮修复；>3 才认为循环
- **watchdog 5min warn 不 escalate**：给人工介入时间，30min 才硬 escalate
- **_diagnose_pod 用 K8s events API**：避免 subprocess kubectl，与现有 kubernetes asyncio 一致
- **PVC GC 独立函数**：runner GC（pod orphan）与 PVC GC 关注点不同，分离易测试
