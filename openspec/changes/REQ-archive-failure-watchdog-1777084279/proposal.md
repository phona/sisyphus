# REQ-archive-failure-watchdog-1777084279: feat(watchdog): detect done-archive session.failed + escalate with reason archive-failed

## 问题

`done-archive` 阶段的 BKD agent 失败时（合 PR / openspec apply 出错 / agent 主动 abort），
sisyphus 的兜底链路最终都把它打成通用 reason：

- BKD 真发 `session.failed` webhook → escalate.py canonical 分支 → `reason="session-failed"`
- BKD 漏发 webhook → watchdog 兜底 stuck 检测 → `body.event="watchdog.stuck"` → `reason="watchdog-stuck"`

两条路都掩盖了"是 archive 阶段失败"这个关键事实。M7 dashboard 04-fail-kind-distribution
按 `reason` 分组统计——archive 失败被淹没在 `session-failed` / `watchdog-stuck` 里，
没法独立看到"过去 7 天 N 次 done-archive 失败"这种针对性指标，团队很难判定
done-archive prompt / 业务 repo 的 archive Makefile target 是否需要优化。

## 方案

给 ARCHIVING 阶段的失败配一个独立 canonical signal：`archive.failed` → `archive-failed`。

### watchdog.py
- 新增 `_STATE_FAILURE_EVENT: dict[ReqState, str]` 映射，目前只列 `ARCHIVING → "archive.failed"`
- `_check_and_escalate` 构造 `_SyntheticBody` 时按 state 取对应 event；非 ARCHIVING 仍是 `"watchdog.stuck"`
- 不动 `_STATE_ISSUE_KEY`、_SKIP_STATES、SQL 等，最小改动

### escalate.py
- `_CANONICAL_SIGNALS` 加 `"archive.failed"` —— body.event 直接 slug 化得到 `"archive-failed"`
- `_TRANSIENT_REASONS` 加 `"archive-failed"` 和 `"archive-failed-after-2-retries"`
- `_is_transient` 把 `body_event=="archive.failed"` 也判 transient（auto-resume 一次）
- BKD 真发 session.failed webhook 路径：当 `body.issueId == ctx.archive_issue_id` 时
  把 reason override 为 `"archive-failed"`
- final_reason bumping：retry 用完时若原 reason 是 `archive-failed` →
  `archive-failed-after-2-retries`（区别于通用 `session-failed-after-2-retries`）
- `is_session_failed_path` 改用 `_CANONICAL_SIGNALS` 集合（自动包含 `archive.failed`）

### state.py
不动。SESSION_FAILED 已经在 ARCHIVING 上有 self-loop transition（state.py 213-223），
escalate action 自己手 CAS 推到 ESCALATED。

## 取舍

- **不引入新 Event 枚举值**：复用 `Event.SESSION_FAILED`，state machine 不需改 transition table。
  reason 细分纯走 escalate action 内部逻辑 + body.event 字符串区分，最小爆炸半径。
- **保留 auto-resume 行为**：archive 阶段失败可能是 BKD agent transient 卡死 / GitHub API 5xx，
  跟其他阶段一样给两次 follow-up "continue" 续作业的机会。不为了 reason 细分牺牲恢复能力。
- **ctx.archive_issue_id 匹配做二次 override**：webhook session.failed 路径没法靠 body.event
  自标记，但 done_archive action 已经把 archive_issue_id 落 ctx，issue id 匹配比再读
  req_state（多一次 DB 调用）便宜且无 race。
- **dashboard 不改**：04-fail-kind-distribution 已经按 `reason:*` tag value group by，
  新出现的 `archive-failed` / `archive-failed-after-2-retries` 自动多两条柱子。
