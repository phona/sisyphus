## ADDED Requirements

### Requirement: snapshot loop 支持配置驱动的 project_id 排除清单

The system SHALL allow operators to declare a list of BKD `project_id` values
that the bkd_snapshot loop MUST skip. The list MUST be provided through the
setting `snapshot_exclude_project_ids` (env var
`SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS`, comma-separated). Each iteration of
`sync_once` MUST filter the project IDs read from `req_state` against this
exclude list before issuing any BKD `list-issues` call. Any project_id present
in the exclude list MUST NOT generate a `snapshot.list_failed` warning, MUST
NOT consume BKD API quota, and MUST NOT be reported in the
`snapshot.synced.projects` log line.

#### Scenario: ORCHN-S1 排除单个项目时跳过 BKD 调用

- **GIVEN** `req_state` 里的 distinct project_id 是 `["alive-1", "77k9z58j"]`
- **AND** `settings.snapshot_exclude_project_ids = ["77k9z58j"]`
- **WHEN** `sync_once()` 被调用
- **THEN** `BKDClient.list_issues` 仅以 `"alive-1"` 调一次，不再以 `"77k9z58j"` 调
- **AND** `snapshot.list_failed` 不被记录

#### Scenario: ORCHN-S2 排除清单为空时保持原行为

- **GIVEN** `settings.snapshot_exclude_project_ids = []`
- **AND** `req_state` 里的 distinct project_id 是 `["alive-1", "alive-2"]`
- **WHEN** `sync_once()` 被调用
- **THEN** `BKDClient.list_issues` 被调用两次，每个 project_id 一次

#### Scenario: ORCHN-S3 全部 project_id 都被排除时短路返回 0

- **GIVEN** `settings.snapshot_exclude_project_ids = ["only-proj"]`
- **AND** `req_state` 里 distinct project_id 仅为 `["only-proj"]`
- **WHEN** `sync_once()` 被调用
- **THEN** 返回 0，且 `BKDClient.list_issues` 不被调用

### Requirement: runner_gc 在 nodes:list 缺权限时优雅降级

The system SHALL detect Kubernetes API 403 (Forbidden) responses on the
`nodes:list` call inside `runner_gc.gc_once` and treat them as a permanent
RBAC denial for the orchestrator's ServiceAccount. On the first occurrence of
a 403, the system MUST emit exactly one `runner_gc.disk_check_rbac_denied`
warning. On all subsequent GC ticks, the system MUST skip the disk-pressure
check entirely without issuing the underlying `list_node` API call and without
emitting any further log line for the disabled probe. The skip MUST be
equivalent to `disk_pressure=False`, so retention-based GC behavior is
preserved. Other (non-403) exceptions raised by `node_disk_usage_ratio` MUST
continue to be logged at debug level (`runner_gc.disk_check_failed`) without
disabling future probes.

#### Scenario: ORCHN-S4 首次 403 时 warn 一次并禁用后续 disk-check

- **GIVEN** orchestrator 进程刚启动，disk-check 还未禁用
- **AND** `core_v1.list_node` 抛出 `ApiException(status=403)`
- **WHEN** `gc_once()` 被调用
- **THEN** `log.warning("runner_gc.disk_check_rbac_denied", ...)` 被打一次
- **AND** 进程级 flag 标记 disk-check 已禁用
- **AND** 返回结果 `disk_pressure=False`

#### Scenario: ORCHN-S5 disk-check 已禁用后 gc_once 不再调 list_node

- **GIVEN** 上一轮 GC 已把 disk-check 禁用
- **WHEN** `gc_once()` 再次被调用
- **THEN** `node_disk_usage_ratio` 不被调用
- **AND** 没有任何 `runner_gc.disk_check_*` 日志被记录
- **AND** 返回结果 `disk_pressure=False`

#### Scenario: ORCHN-S6 非 403 异常仍走 debug 不禁用

- **GIVEN** `node_disk_usage_ratio` 抛出 `ApiException(status=500)`
- **WHEN** `gc_once()` 被调用
- **THEN** `log.debug("runner_gc.disk_check_failed", ...)` 被记录
- **AND** disk-check 进程级 flag 保持未禁用（下一轮仍会再尝试）

#### Scenario: ORCHN-S7 disk-check 正常 ratio > threshold 时仍能触发紧急清理

- **GIVEN** RBAC 健全（list_node 200 OK），`ratio=0.9`，threshold=0.8
- **WHEN** `gc_once()` 被调用
- **THEN** `runner_gc.disk_pressure` warning 被记录
- **AND** 返回结果 `disk_pressure=True`
