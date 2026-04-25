# router-noise-filter Specification

## Purpose
TBD - created by archiving change REQ-router-noise-filter-1777109307. Update Purpose after archive.
## Requirements
### Requirement: webhook 必须早期 skip 不带 REQ tag 也不带 intent 入口 tag 的 issue.updated

The webhook handler MUST evaluate, after tag resolution and before invoking
`router.derive_event` or `observability.record_event`, whether the incoming
`issue.updated` event is relevant to any sisyphus REQ workflow. The handler
MUST treat the event as noise and short-circuit when **all** of the following
hold:

- `body.event == "issue.updated"`
- `router.extract_req_id(tags)` returns `None` (no `REQ-*` tag in `tags`)
- `tags` contains **neither** `"intent:intake"` **nor** `"intent:analyze"`

When the event is treated as noise, the handler MUST:

1. Call `dedup.mark_processed(pool, eid)` so BKD's at-least-once retry of the
   same event ID also short-circuits without reprocessing.
2. Return `{"action": "skip", "reason": "issue.updated without REQ or intent tag"}`.
3. NOT call `observability.record_event("webhook.received", ...)` so noise
   events do not pollute the event log.
4. NOT call `router.derive_event` or any downstream `engine.step` / state
   machine code path.

The existing `session.completed` noise filter (skip when no REQ tag) MUST
continue to behave exactly as before. The two filters MUST coexist as parallel
branches of the same early-noise section in `webhook.py`.

#### Scenario: RNF-S1 issue.updated 无 REQ tag 无 intent tag → skip

- **GIVEN** webhook 收到 `body.event="issue.updated"`，`tags=["bug", "frontend"]`
- **WHEN** handler 执行到 noise filter 段
- **THEN** handler 返回 `{"action": "skip", "reason": "issue.updated without REQ or intent tag"}`
- **AND** `dedup.mark_processed` 被调用一次（event_id 即本次 webhook 的 eid）
- **AND** `obs.record_event("webhook.received", ...)` 没有被调用
- **AND** `router.derive_event` 没有被调用
- **AND** `engine.step` 没有被调用

#### Scenario: RNF-S2 issue.updated 含 REQ tag → 走下游

- **GIVEN** webhook 收到 `body.event="issue.updated"`，`tags=["REQ-foo", "analyze"]`
- **WHEN** handler 执行
- **THEN** noise filter 不命中，handler 继续调 `obs.record_event` + `derive_event` +
  `engine.step`（按现有路径推进状态机；engine.step 至少被调用一次）

#### Scenario: RNF-S3 issue.updated 仅含 intent:intake tag → 走下游

- **GIVEN** webhook 收到 `body.event="issue.updated"`，`tags=["intent:intake"]`（无 REQ-*）
- **WHEN** handler 执行
- **THEN** noise filter 不命中（intent 入口必须能 fire INTENT_INTAKE），handler 继续走
  下游：`derive_event` 返回 `Event.INTENT_INTAKE` → `engine.step` 被调用

#### Scenario: RNF-S4 issue.updated 仅含 intent:analyze tag → 走下游

- **GIVEN** webhook 收到 `body.event="issue.updated"`，`tags=["intent:analyze"]`（无 REQ-*）
- **WHEN** handler 执行
- **THEN** noise filter 不命中，`derive_event` 返回 `Event.INTENT_ANALYZE` → `engine.step`
  被调用

#### Scenario: RNF-S5 session.completed 无 REQ tag → 旧 filter 仍生效

- **GIVEN** webhook 收到 `body.event="session.completed"`，`tags=["analyze"]`（无 REQ-*）
- **WHEN** handler 执行
- **THEN** 命中既有 session.completed noise filter，返回 `{"action": "skip", "reason": "session event without REQ tag"}`，`dedup.mark_processed` 被调用，`engine.step` 未调用

