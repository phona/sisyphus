# REQ-feat-stuck-notify-378-v2-1777866642

feat(watchdog): ESCALATED stuck-REQ notify (closes #378)

## Why

REQ 一进 ESCALATED 就停在那等人 resume。设计上是这样，可问题在于：人没在线 / 忘了 /
没人盯就**永久卡**。issue #378 实证：v5 卡 30min 才被发现是 router 静默 drop。

watchdog 现在三件事——`_tick`（卡死 stage 兜底 escalate）、`_sync_stuck_sub_agent_statuses_tick`
（BKD 状态补偿）、`_mark_abandoned_escalated_reqs`（7 天后标 abandoned-by-user）——
都不解决"刚进 ESCALATED 没人理"的检测延迟。7 天 abandoned 太晚；watchdog escalate
路径只发 GH incident（如配了 `gh_incident_repo`）但不主动 nudge 操作员。

## What Changes

### 1. watchdog 新 loop：`_notify_stale_escalated_tick`

每 N 个 tick（搭 `_sync_stuck_sub_agent_statuses_tick` 同周期，5 ticks ≈ 5min）扫一次
`req_state where state='escalated' AND updated_at < NOW - escalated_stale_threshold_sec`。
对每条命中的 row：

- 写一条 `obs.record_event(kind="watchdog_stuck_notify", req_id=..., extras={...})`
- 发一条 `log.warning("watchdog.stuck_notify", ...)`，dashboards / Loki 一眼看
- 若 `escalated_stale_telegram_url` 配了，POST 一条 markdown 文本到该 URL
- 把"已通知"标记写回 `req_state.context.stuck_notified_at = now()`，避免每 tick 重发

幂等通过 `context.stuck_notified_at` 与 `req_state.updated_at` 比较保证：每段
ESCALATED 期内**至多通知一次**——一旦 REQ 被 resume → 推进 → 再次 escalate，
`updated_at` 会跳到新时刻，watermark 自动失效，新一轮 stale 又能通知。

### 2. 新配置（沿用现有 watchdog_* 命名风格）

`config.py` 新增：

- `escalated_stale_notify_enabled: bool = True` — 关掉整套通知（dev 模式可关）
- `escalated_stale_threshold_sec: int = 1800` — 30 min（issue #378 提议值）
- `escalated_stale_telegram_url: str = ""` — 可选 webhook，空 = 不外推（默认安全）

### 3. helm values + configmap pass-through

`orchestrator/helm/values.yaml` 加 3 个对应 env key（webhook URL 默认空字符串），
`orchestrator/helm/templates/configmap.yaml` pass through。Telegram URL 走 `if`
条件（空就不渲染 env，沿用现有 `gh_incident_repo` 写法）。

## Out of scope

- **多 channel notify**（Slack / 飞书 / Discord）：v2 只做 1 个 webhook URL。
  Telegram bot 的 `sendMessage` API 跟通用 webhook 同形（POST JSON），格式
  设计上 webhook URL 就是 `https://api.telegram.org/bot<TOKEN>/sendMessage`
  附带 `chat_id` 在 payload 里——helm operator 可以包一层 nginx redirect 把
  其他 webhook 桥过来。
- **重复通知 / 升级路径**（每 N 小时再 nudge）：issue #378 没要求。已有
  `_mark_abandoned_escalated_reqs` 7 天 hard cap，足够 long-tail 兜底。
- **GH issue 上贴 comment**：原 issue body 提到 sisyphus-internal channel，
  但当前 sisyphus 唯一"内部 channel" = 已在 `gh_incident.py` 里 escalate 时
  开的 GH incident issue。这条 PR 不动 gh_incident 路径。

## 关联

- #378 本 REQ 对应 GH issue
- #353 `_sync_stuck_sub_agent_statuses_tick`（同区代码 — 共享 tick 周期）
- `_mark_abandoned_escalated_reqs`（7 天 hard cap，长 tail 互补）
- REQ-impl-gh-incident-open-1777173133 / `gh_incident.py`（escalate 时开 GH issue —— 跟本 REQ 互补）
