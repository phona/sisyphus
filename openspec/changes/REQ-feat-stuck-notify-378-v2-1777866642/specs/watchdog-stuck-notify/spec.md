# Spec Delta — watchdog ESCALATED stuck-REQ notify

## ADDED Requirements

### Requirement: Watchdog emits exactly one notification per ESCALATED stale window

The orchestrator watchdog SHALL run a periodic `_notify_stale_escalated_tick`
that scans `req_state` for rows where `state='escalated'` and
`updated_at < NOW() - escalated_stale_threshold_sec`. For each row matched, the
watchdog MUST emit exactly one notification per stale window: the function
SHALL skip rows whose `req_state.context.stuck_notified_at` is set and is
greater than or equal to `req_state.updated_at` (meaning the current ESCALATED
window has already been notified). After sending a notification the watchdog
SHALL persist `stuck_notified_at = now (UTC ISO-8601)` back into
`req_state.context` so subsequent ticks become no-ops until the REQ leaves
ESCALATED (resume → progress → re-escalate resets `updated_at`, which in turn
re-arms the notifier).

The notification body produced for each stale REQ MUST contain the REQ id, the
human-readable elapsed seconds since `req_state.updated_at`, and a hint to
inspect the verifier issue. The body string SHALL begin with the literal
prefix `"⏰ "` so dashboards can string-match it.

#### Scenario: WSN-S1 first stale tick notifies once and persists watermark

- **GIVEN** a `req_state` row with `state='escalated'`, `updated_at = now - 2400s`, `context = {}` (no `stuck_notified_at`)
- **AND** `escalated_stale_threshold_sec = 1800`
- **WHEN** `watchdog._notify_stale_escalated_tick` runs
- **THEN** exactly one `obs.record_event` call MUST be made with `kind="watchdog_stuck_notify"` and the matching `req_id`
- **AND** a `log.warning` event named `watchdog.stuck_notify` MUST be recorded
- **AND** the message body produced MUST start with the literal prefix `"⏰ "` and contain the REQ id
- **AND** `req_state.context.stuck_notified_at` MUST be persisted to a UTC ISO-8601 string

#### Scenario: WSN-S2 second tick within same stale window is suppressed

- **GIVEN** the same row as WSN-S1 but `context.stuck_notified_at` already set to a value `>= req_state.updated_at`
- **WHEN** `_notify_stale_escalated_tick` runs again
- **THEN** zero `obs.record_event` calls MUST be made
- **AND** zero HTTP POSTs to the telegram URL MUST occur
- **AND** the function MUST return `{"checked": >=1, "notified": 0}`

#### Scenario: WSN-S3 telegram POST is best-effort and never raises

- **GIVEN** `escalated_stale_telegram_url` is set to a URL whose POST raises `httpx.ConnectError`
- **AND** a row qualifies for notification (state=escalated, stuck > threshold, no watermark)
- **WHEN** `_notify_stale_escalated_tick` runs
- **THEN** the function MUST NOT raise out of the tick
- **AND** `obs.record_event` MUST still be invoked once (in-DB notification is the source of truth)
- **AND** `req_state.context.stuck_notified_at` MUST still be persisted (do not retry next tick — telegram is opportunistic)
- **AND** a `log.warning` event named `watchdog.stuck_notify.telegram_failed` MUST be recorded

#### Scenario: WSN-S4 disabled flag short-circuits the tick

- **GIVEN** `settings.escalated_stale_notify_enabled = False`
- **AND** at least one row would otherwise qualify
- **WHEN** `_notify_stale_escalated_tick` runs
- **THEN** the function MUST return `{"checked": 0, "notified": 0}` without calling `pool.fetch` or `obs.record_event`
