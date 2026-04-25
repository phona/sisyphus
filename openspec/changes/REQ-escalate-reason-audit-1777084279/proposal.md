# REQ-escalate-reason-audit-1777084279: fix(escalate): correctly set ctx.escalated_reason on all escalate paths

## 问题

被 escalate 的 REQ 在 `req_state.context.escalated_reason` 写入的值常常**不反映真实失败原因**：

- `INTAKE_FAIL` 走 escalate 时，`ctx.escalated_reason` 落 `"session-completed"` 或 `"issue-updated"`（取决于触发 webhook 的 `body.event`）
- `PR_CI_TIMEOUT` 走 escalate 时，落 `"session-completed"`（pr-ci-watch checker 完成时的 webhook 类型）
- `ACCEPT_ENV_UP_FAIL` 走 escalate 时，落 `create_accept` action 调用上下文里的 `body.event`（一般是 `"session-completed"`）
- `VERIFY_ESCALATE` 走 escalate 时，落 `"session-completed"`（verifier issue 完成时的 webhook 类型）

只有 `SESSION_FAILED`（含 watchdog.stuck）和 `engine._emit_escalate` 注 `action-error:...` 这两条路径写入的 reason 是正确的。

下游影响：
- `failure_mode` view（`migrations/0002_observability_views.sql:63`）按 `context->>'escalated_reason'` 聚合，结果里"intake-fail / pr-ci-timeout / verifier-decision-escalate"被错误归并到"session-completed / issue-updated"桶，看板失去信号
- BKD intent issue 上 `reason:<细分>` tag 同步被污染（escalate.py 同时写 ctx 和 tag），人工排查走查 tag 也定位不到真实失败 stage
- escalate.py 里 `_is_transient` 对 `"verifier-decision-escalate"` 的特判形同虚设——实际从未匹配过

## 根因

`actions/escalate.py` 决定 `reason` 时：
```python
if body.event in _CANONICAL_SIGNALS:  # {"session.failed", "watchdog.stuck"}
    reason = body.event.replace(".", "-")[:40]
else:
    reason = (ctx or {}).get("escalated_reason") or (
        (body.event or "unknown").replace(".", "-")[:40]
    )
```

`body.event` 是 **BKD webhook 类型**（`session.completed` / `issue.updated` / `session.failed` / 合成的 `watchdog.stuck`），跟状态机的 `Event` 不同步。除 `SESSION_FAILED` 外，其他 escalate transition（`INTAKE_FAIL` / `PR_CI_TIMEOUT` / `ACCEPT_ENV_UP_FAIL` / `VERIFY_ESCALATE`）触发时 `body.event` 都不是 canonical 信号，且没有 caller 提前向 ctx 写 `escalated_reason`，于是 fallback 退到 `body.event.replace(".", "-")` 拿到无意义的 webhook 类型 slug。

## 方案

在 `engine.step` 调度 `escalate` action **之前**，根据触发本次 transition 的状态机 `Event`，预填 `ctx.escalated_reason` 为该 Event 的 canonical reason slug。

新增 `engine._EVENT_TO_ESCALATE_REASON: dict[Event, str]`：

| Event | reason slug |
|---|---|
| `INTAKE_FAIL` | `intake-fail` |
| `PR_CI_TIMEOUT` | `pr-ci-timeout` |
| `ACCEPT_ENV_UP_FAIL` | `accept-env-up-fail` |
| `VERIFY_ESCALATE` | `verifier-decision-escalate` |

`SESSION_FAILED` 不入表——`escalate.py` 已通过 `body.event in {"session.failed", "watchdog.stuck"}` 的 canonical 分支正确处理。

预填规则：
- `transition.action == "escalate"` 且 `event in _EVENT_TO_ESCALATE_REASON` → 写入对应 slug
- `ctx.escalated_reason` 已经是 `"action-error:..."`（`_emit_escalate` 注的）→ 不覆盖（保留更具体的 action 异常信息）
- 其他情况不动

## 取舍

- **改 engine 不改 escalate.py 主路径**：reason 决策逻辑保留在 escalate.py（canonical 信号 + ctx 优先），engine 只是给非 canonical 路径补一个 ctx 字段。`escalate.py` 本身签名 / 退化逻辑都不变。
- **不改 action handler 接口**：不给 action handler 加 `event` kwarg（那要改所有 action 注册），用 ctx 通道传 reason 是现成机制。
- **`SESSION_FAILED` 路径不预填**：它的 reason 来源更精细（`session.failed` vs `watchdog.stuck`，以及 `_emit_escalate` 注的 `action-error:...`），由 `escalate.py` 用 `body.event` canonical 信号判，预填会破坏现有正确逻辑。
- **不引入 retry 语义变更**：`_is_transient` 表保持原样（只对 `session.failed` / `watchdog.stuck` / `action-error:...` 返 True）。修复后 `INTAKE_FAIL` / `PR_CI_TIMEOUT` 等仍 non-transient（直接真 escalate，不 auto-resume），观测语义不变。
- **新值跟 `_is_transient` 现有 `"verifier-decision-escalate"` 特判对齐**：现在这条特判终于能匹配到运行时值，从死代码变成真生效（仍然是"非 transient"，没行为变化，只是 dispatch 路径短了一步）。
