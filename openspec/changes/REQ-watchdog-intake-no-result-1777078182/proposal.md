# REQ-watchdog-intake-no-result-1777078182: feat(watchdog): detect intake session.completed without result tag

## 问题

intake-agent session.completed 后**必须** PATCH `result:pass` 或 `result:fail` tag，
router (`derive_event` for `session.completed` + `intake` tag) 才会派发 `INTAKE_PASS` /
`INTAKE_FAIL`。如果 agent 忘了这一步：

- `session.completed` webhook 来了，tags 不含 result:* → router 返回 `None` → 无 event fire
- 后续也没有 `issue.updated` 事件触发 race fallback（agent 已退出）
- REQ state 永远卡在 `INTAKING`

watchdog 的通用 stuck 检测会兜底，但有两个问题：
1. body.event=`watchdog.stuck` 是 `_CANONICAL_SIGNALS`，escalate.py 把 reason 锁成
   `watchdog-stuck`，无法体现这是个 prompt-bug（intake-agent 实现没收尾）；
2. `watchdog-stuck` 被 `_is_transient` 判为 transient → 走 auto-resume follow-up
   "continue, you were interrupted"。但 intake session 已经 completed，没有进程在跑，
   follow-up 唤不醒，浪费 2 次 retry 才进真 escalated。

## 方案

### 检测

watchdog 在通用兜底之前，先识别 intake-no-result-tag 这一具体形态：

```
state == INTAKING
  AND BKD issue.session_status != "running"
  AND issue.tags ∩ {"result:pass", "result:fail"} == ∅
```

满足 → 走专属路径（reason="intake-no-result-tag"），不满足 → 走原通用 watchdog_stuck 路径。

### 专属 escalate 通路

新 body.event = `watchdog.intake_no_result_tag`：
- 不在 `_CANONICAL_SIGNALS` → escalate.py 优先采用 ctx.escalated_reason（watchdog
  预先 PATCH 进 ctx 的 `intake-no-result-tag`），最终 BKD intent issue 上加
  `escalated` + `reason:intake-no-result-tag`；
- 不在 `_TRANSIENT_REASONS` 也不匹配 `_is_transient` 的 body.event 分支 → 跳过
  auto-resume，直接真 escalate；
- 加入 `_SESSION_END_SIGNALS`（即原 `is_session_failed_path` 集合）→ escalate
  末尾的手动 CAS → ESCALATED + `cleanup_runner` 仍跑，REQ 进终态。

### artifact_checks 落表

写一条 `artifact_checks`：`stage="watchdog:intake-no-result-tag"`，
`reason="intake-no-result-tag"`，`stderr_tail` 含 session_status + stuck 秒数，
M7 dashboard 可分桶看这条 prompt-bug 的发生频率。

## 取舍

- **不改 router**：router 看到 session.completed 无 result tag 主动 escalate 也可行，
  但 router 是同步阻塞 webhook handler 的路径，引入 BKD 二次调用 / artifact 写入会
  拖慢主链。watchdog 异步独立，更适合兜底语义。
- **BKD 查不到 issue 时降级走通用 watchdog_stuck**：保持原有 auto-resume 兜底，
  避免误判（issue 真删了的极端场景下 ctx 不脏化）。
- **不识别"有 result tag 但卡住"的形态**：那是 router 漏 fire 的另一类问题，
  超本 REQ 范围，仍走通用 stuck 路径（人工排查）。
