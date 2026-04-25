## ADDED Requirements

### Requirement: webhook dedup 支持 at-least-once retry（handler 崩溃后允许重试）

The system SHALL implement a three-state dedup protocol for webhook event processing. Upon receiving a webhook event, the system MUST perform an atomic check-and-record operation that distinguishes between: (a) a brand-new event that has never been seen before ("new"), (b) an event that was previously recorded but whose handler crashed before completion ("retry"), and (c) an event that was already successfully processed ("skip"). Only the "skip" state MUST result in the event being discarded without processing.

The system SHALL record a `processed_at` timestamp in the `event_seen` table only after the webhook handler completes successfully. Any exception or crash before completion MUST leave `processed_at` as NULL, allowing subsequent BKD redeliveries to re-enter the processing path via the "retry" state.

#### Scenario: DEDUP-S1 全新事件插入返回 new

- **GIVEN** `event_seen` 表中不存在 event_id `evt-abc`
- **WHEN** 调用 `check_and_record(pool, "evt-abc")`
- **THEN** 返回 `"new"`，event_seen 中插入一行 processed_at=NULL

#### Scenario: DEDUP-S2 已成功处理的事件重发返回 skip

- **GIVEN** `event_seen` 表中存在 event_id `evt-abc`，processed_at IS NOT NULL
- **WHEN** 调用 `check_and_record(pool, "evt-abc")`
- **THEN** 返回 `"skip"`

#### Scenario: DEDUP-S3 首次处理崩溃后重发返回 retry

- **GIVEN** `event_seen` 表中存在 event_id `evt-abc`，processed_at IS NULL（上次 handler 崩溃）
- **WHEN** 调用 `check_and_record(pool, "evt-abc")`
- **THEN** 返回 `"retry"`，handler 继续执行

#### Scenario: DEDUP-S4 handler 成功后 mark_processed 标记

- **GIVEN** webhook handler 成功处理完成
- **WHEN** `mark_processed(pool, event_id)` 被调用
- **THEN** `event_seen` 中对应行的 `processed_at` 被设置为 NOW()

#### Scenario: DEDUP-S5 handler 崩溃时 mark_processed 不被调用

- **GIVEN** webhook 调用 `engine.step` 时抛出异常
- **WHEN** 异常传播到调用方
- **THEN** `mark_processed` 未被调用，processed_at 保持 NULL，BKD 重发时走 retry 路径

#### Scenario: DEDUP-S6 retry 路径下状态机 CAS 幂等保护

- **GIVEN** 状态机已在首次处理时成功推进（state 已变）
- **WHEN** retry 路径重新触发 engine.step
- **THEN** CAS 失败（expected != actual state），engine 返回 skip，不双触发 action
