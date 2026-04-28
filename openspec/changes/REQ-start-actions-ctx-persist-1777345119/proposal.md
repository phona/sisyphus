# REQ-start-actions-ctx-persist-1777345119: fix(watchdog+start_challenger): persist challenger_issue_id to ctx + watchdog defensive guard

## 问题

`start_challenger.py` 在创建 challenger BKD issue 后，没有调用 `req_state.update_context()` 把 `challenger_issue_id` 写入 REQ context。

结果：`watchdog.py` 的 `_check_and_escalate` 在 `_STATE_ISSUE_KEY[CHALLENGER_RUNNING]` 查 ctx key 时，得到 `issue_id=None`；因为 `issue_id is None` 时跳过 BKD session 查询，直接走 escalate 路径，**即使 challenger agent 仍在运行**。

实证：issues #474 / #467 — challenger 刚起约 5 min（`watchdog_session_ended_threshold_sec` 阈值）被 watchdog 误判为 stuck 并 escalate，challenger agent 仍 session_status=running。

根因拆解：
1. **ctx-write 缺失**：`start_challenger.py` 无 `db` / `req_state` import，issue.id 从未落 ctx。
2. **`_STATE_ISSUE_KEY` 缺失 CHALLENGER_RUNNING**：watchdog 不知道 challenger 有对应 ctx key，无法查 BKD session status。
3. **无防守性跳过**：`issue_id=None` 时 watchdog 直接 escalate，而正确行为应是"跳过，等 ctx key 落地"。

## 方案

### 修复 1：start_challenger.py 写 ctx（主链修复）

`start_challenger` 在 `bkd.update_issue` 之后调用：

```python
pool = db.get_pool()
await req_state.update_context(pool, req_id, {"challenger_issue_id": issue.id})
```

这确保 CHALLENGER_RUNNING 进 watchdog 扫描窗口之前，ctx 里已有 `challenger_issue_id`。

### 修复 2：watchdog `_STATE_ISSUE_KEY` 补 CHALLENGER_RUNNING

```python
_STATE_ISSUE_KEY: dict[ReqState, str | None] = {
    ...
    ReqState.CHALLENGER_RUNNING: "challenger_issue_id",
    ...
}
```

watchdog 能用 `challenger_issue_id` 查 BKD session 状态，再决定是否 escalate。

### 修复 3：watchdog CHALLENGER_RUNNING 防守性 skip（defense-in-depth）

在 `_check_and_escalate` 里，`issue_id` 解析之后、BKD 查询之前：

```python
if state == ReqState.CHALLENGER_RUNNING and issue_id is None:
    log.warning("watchdog.missing_issue_id", ...)
    return False
```

作用域**只限 CHALLENGER_RUNNING**：
- FIXER_RUNNING 等有 fixer-round-cap 安全上限，必须无条件 escalate，不受影响。
- STAGING_TEST / ACCEPT 等无硬性 issue_id 依赖，按原路径走。

## 取舍

- **只改 CHALLENGER_RUNNING**：audit 发现其他 state 的 ctx-write 均已有对应实现，或属于无 BKD issue 的客观 checker（ctx key=None 是设计）。如后续发现其他 action 漏 ctx-write，应单独修。
- **守卫作用域最小化**：broad guard（所有 issue_key≠None 且 issue_id=None 都跳过）会破坏 FIXER_RUNNING 的 fixer-round-cap 安全机制——fixer 可以没有 fixer_issue_id 但仍必须被 watchdog cap 兜底。因此守卫显式 scoped to CHALLENGER_RUNNING。
- **不加 stuck_sec fallback**：不引入"超过 N 分钟就算卡死"的二次 cap。理由：正确 ctx-write 修复后 issue_id 总会在 5 min 内落地；二次 cap 是复杂度换轻微防御增益，不值。
- **不修改 `_SKIP_STATES`**：CHALLENGER_RUNNING 不是终态，应被 watchdog 扫描，修复的是 issue_id 缺失的处理逻辑，不是扫描条件。

## 兼容性

- **既有 CHALLENGER_RUNNING REQ**（ctx 无 `challenger_issue_id`）：watchdog 防守性跳过，不误 escalate，等待下一次 deploy 后新 start_challenger 调用写入正确 ctx key。
- **其他 state**：`_STATE_ISSUE_KEY` 新增一行，其余行不变，行为无影响。
- **测试**：4 个既有 start_challenger 单测 + 1 新测；watchdog 既有单测 + 4 新测。
