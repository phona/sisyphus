## ADDED Requirements

### Requirement: webhook 早期 skip 不得 mark_processed，允许 BKD retry 重新处理

The webhook handler MUST NOT call `dedup.mark_processed` on any early skip or
short-circuit return path. The `mark_processed` call MUST only occur after
`engine.step` completes successfully. This ensures that events which are skipped
due to transient conditions (e.g., missing tags from a race condition) remain
eligible for BKD at-least-once redelivery via the "retry" path.

Specifically, the following four early return paths MUST NOT call
`dedup.mark_processed`:

1. `session.completed` without a `REQ-*` tag (noise filter)
2. `issue.updated` without a `REQ-*` tag and without `intent:intake` or
   `intent:analyze` (noise filter)
3. `event` is `None` after `router.derive_event` (no event mapping)
4. `req_id` is `None` after `router.extract_req_id` (no resolvable REQ ID)

The handler MUST leave `processed_at` as `NULL` on all early skip paths so that
a subsequent BKD redelivery of the same `event_id` returns `retry` from
`check_and_record`, allowing re-evaluation with potentially updated tags or state.

#### Scenario: DDS-S1 早期 skip 不 mark_processed，processed_at 保持 NULL

- **GIVEN** `event_seen` 中不存在 event_id `evt-550`
- **WHEN** webhook handler 收到 `session.completed`，tags 不含 `REQ-*`
- **THEN** handler 返回 `{"action": "skip", "reason": "session event without REQ tag"}`
- **AND** `dedup.mark_processed` **未被调用**
- **AND** `event_seen` 中对应行的 `processed_at` 仍为 `NULL`

#### Scenario: DDS-S2 BKD 重发时走 retry 路径

- **GIVEN** `event_seen` 中存在 event_id `evt-550`，`processed_at IS NULL`
  （DDS-S1 的 skip 残留）
- **WHEN** BKD 重发同 event_id，此时 tags 已含 `REQ-550`
- **THEN** `check_and_record` 返回 `"retry"`
- **AND** handler 继续执行 `engine.step`
- **AND** `engine.step` 成功后 `mark_processed` 被调用，`processed_at` 被设置

#### Scenario: DDS-S3 no_event_mapping 早期 skip 同样不 mark_processed

- **GIVEN** `event_seen` 中不存在 event_id `evt-550b`
- **WHEN** webhook handler 收到事件，`derive_event` 返回 `None`
- **THEN** handler 返回 `{"action": "skip", "reason": "no event mapping"}`
- **AND** `dedup.mark_processed` **未被调用**
- **AND** `event_seen` 中对应行的 `processed_at` 仍为 `NULL`

#### Scenario: DDS-S4 no_req_id 早期 skip 同样不 mark_processed

- **GIVEN** `event_seen` 中不存在 event_id `evt-550c`
- **WHEN** webhook handler 收到事件，`extract_req_id` 返回 `None`
- **THEN** handler 返回 `{"action": "skip", "reason": "no req_id resolvable"}`
- **AND** `dedup.mark_processed` **未被调用**
- **AND** `event_seen` 中对应行的 `processed_at` 仍为 `NULL`
