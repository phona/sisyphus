# REQ-441: feat(watchdog): reconcile BKD sessionStatus before judging stuck — add CHALLENGER_RUNNING to issue map

## 问题

`watchdog._STATE_ISSUE_KEY` 表缺少 `ReqState.CHALLENGER_RUNNING` 条目。

`_check_and_escalate` 在判断 stuck 前会先调 `bkd.get_issue()` 查
`session_status`——但前提是 `_STATE_ISSUE_KEY[state]` 返回非 None 的 ctx key，
watchdog 才能找到对应的 BKD issue ID。

`CHALLENGER_RUNNING` 不在这张表里，导致：
- `issue_key = _STATE_ISSUE_KEY.get(ReqState.CHALLENGER_RUNNING)` → Python 默认 `None`
- `issue_id = None`
- BKD **不被查询**，`still_running` 永远是 `False`
- watchdog 对正在运行中的 challenger session 也套用 `ended_sec=300` 快车道阈值
- 超 300s 未收到 webhook 就立即 escalate，杀掉真正在运行的 challenger-agent

而 `start_challenger.py` 在创建 challenger issue 后已将 `challenger_issue_id`
写入 req ctx（`return {"challenger_issue_id": issue.id, ...}`）——信息存在但
watchdog 没读。

## 方案

在 `_STATE_ISSUE_KEY` 加一行：

```python
ReqState.CHALLENGER_RUNNING: "challenger_issue_id",
```

这样 `_check_and_escalate` 能正确取出 `ctx["challenger_issue_id"]`，查 BKD
获取 `session_status`，若为 `"running"` 则 skip（保护长尾真运行），若已
ended/failed 才走 `ended_sec` 快车道，与其他 autonomous-bounded stage 行为一致。

## 影响范围

- `orchestrator/src/orchestrator/watchdog.py` — `_STATE_ISSUE_KEY` 新增一行
- `orchestrator/tests/test_watchdog.py` — 新增 3 个 CHALLENGER_RUNNING 相关 case
