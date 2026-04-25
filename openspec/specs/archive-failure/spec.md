# archive-failure Specification

## Purpose
TBD - created by archiving change REQ-archive-failure-watchdog-1777084279. Update Purpose after archive.
## Requirements
### Requirement: watchdog 在 ARCHIVING 状态贴 archive 专属失败信号

The watchdog SHALL emit `body.event="archive.failed"` (instead of the generic
`"watchdog.stuck"`) when escalating a stuck REQ whose current state is `ARCHIVING`.
The synthetic body MUST still carry `Event.SESSION_FAILED` so the state machine's
existing `(ARCHIVING, SESSION_FAILED)` self-loop transition fires unchanged; only
the `body.event` string used by `escalate` action for reason derivation differs.
For all other in-flight states the watchdog MUST keep emitting the generic
`"watchdog.stuck"` event so existing behavior is preserved.

#### Scenario: ARCH-S1 ARCHIVING 卡死 + session=failed → body.event="archive.failed"

- **GIVEN** `req_state` 表中存在 REQ `REQ-arch-1`，state=`archiving`，updated_at 超过
  `watchdog_stuck_threshold_sec`，ctx 含 `archive_issue_id="arch-1"`
- **AND** BKD `get_issue("arch-1")` 返回 `session_status="failed"`
- **WHEN** `watchdog._tick()` 触发
- **THEN** `engine.step` 被调用一次，`body.event == "archive.failed"`，`event == Event.SESSION_FAILED`，
  `cur_state == ReqState.ARCHIVING`

#### Scenario: ARCH-S2 非 ARCHIVING state 仍贴 generic watchdog.stuck

- **GIVEN** REQ `REQ-st-1`，state=`staging-test-running` 卡死，session=failed
- **WHEN** `watchdog._tick()` 触发
- **THEN** `engine.step` 被调用一次，`body.event == "watchdog.stuck"`（非 archive 路径不受影响）

### Requirement: escalate action 把 archive 阶段失败的 reason 标成 archive-failed

The escalate action SHALL produce `reason="archive-failed"` (and tag the intent
issue with `reason:archive-failed`) for any failure of the done-archive BKD
session. This MUST cover both observation paths:

1. The watchdog path, where `body.event == "archive.failed"` (canonical signal added by this change).
2. The BKD-webhook path, where `body.event == "session.failed"` AND the failed
   issue id matches `ctx.archive_issue_id` (recorded by the `done_archive` action).

For unrelated `session.failed` events (issue id does not match `archive_issue_id`)
the escalate action MUST keep the existing `reason="session-failed"` derivation.

The auto-resume policy SHALL apply equally to `archive-failed` failures
(`_is_transient(...)` returns `True`), giving the done-archive agent up to
`_MAX_AUTO_RETRY` follow-up "continue" attempts before final escalation.
After retries are exhausted, the final reason MUST be `archive-failed-after-2-retries`
(distinct from the generic `session-failed-after-2-retries`).

#### Scenario: ARCH-S3 watchdog 路径 archive.failed → reason archive-failed

- **GIVEN** `body.event="archive.failed"`，`ctx={"intent_issue_id":"intent-1","archive_issue_id":"arch-1"}`，
  `auto_retry_count` 缺省为 0
- **WHEN** `escalate(...)` 被调用
- **THEN** 返回 `{"auto_resumed": True, "reason": "archive-failed", "retry": 1}`，
  BKD `follow_up_issue` 被调用一次（"continue" prompt），`merge_tags_and_update` 没被调

#### Scenario: ARCH-S4 BKD session.failed webhook 路径 + issue 匹配 → reason archive-failed

- **GIVEN** `body.event="session.failed"`，`body.issueId="arch-2"`，
  `ctx={"intent_issue_id":"intent-1","archive_issue_id":"arch-2"}`，retry=0
- **WHEN** `escalate(...)` 被调用
- **THEN** 返回 `{"auto_resumed": True, "reason": "archive-failed", "retry": 1}`

#### Scenario: ARCH-S5 session.failed 但 issue 不是 archive 的 → reason 保持 session-failed

- **GIVEN** `body.event="session.failed"`，`body.issueId="dev-1"`，
  `ctx={"archive_issue_id":"arch-other"}`（不匹配）
- **WHEN** `escalate(...)` 被调用
- **THEN** 返回的 reason 仍是 `"session-failed"`（不被 archive override 误标）

#### Scenario: ARCH-S6 archive-failed retry 用完 → final reason archive-failed-after-2-retries

- **GIVEN** `body.event="archive.failed"`，`ctx={"archive_issue_id":"arch-1","auto_retry_count":2}`，
  当前 state=`archiving`
- **WHEN** `escalate(...)` 被调用
- **THEN** 返回 `{"escalated": True, "reason": "archive-failed-after-2-retries"}`，
  intent issue 被加 tag `reason:archive-failed-after-2-retries`，REQ state CAS 到 `ESCALATED`

