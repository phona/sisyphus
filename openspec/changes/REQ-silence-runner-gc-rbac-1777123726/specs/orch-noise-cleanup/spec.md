## MODIFIED Requirements

### Requirement: runner_gc 在 nodes:list 缺权限时优雅降级

The system SHALL detect Kubernetes API 403 (Forbidden) responses on the
`nodes:list` call inside `runner_gc.gc_once` and treat them as a permanent
RBAC denial for the orchestrator's ServiceAccount. On the first occurrence of
a 403 within a process lifetime, the system MUST emit **at most one**
`runner_gc.disk_check_rbac_denied` log line at **INFO** level (not WARNING) —
this MUST NOT be raised at WARNING level so that operator alert dashboards
filtering for warnings stop receiving repeated noise across orchestrator pod
restarts. On all subsequent GC ticks within the same process, the system MUST
skip the disk-pressure check entirely without issuing the underlying
`list_node` API call and without emitting any further log line for the
disabled probe. The skip MUST be equivalent to `disk_pressure=False`, so
retention-based GC behavior is preserved. Other (non-403) exceptions raised
by `node_disk_usage_ratio` MUST continue to be logged at debug level
(`runner_gc.disk_check_failed`) without disabling future probes.

#### Scenario: ORCHN-S4 首次 403 时 info 一次并禁用后续 disk-check

- **GIVEN** orchestrator 进程刚启动，`_DISK_CHECK_DISABLED` 为 False
- **AND** `core_v1.list_node` 抛出 `ApiException(status=403)`
- **WHEN** `gc_once()` 被调用
- **THEN** 恰好一条 `runner_gc.disk_check_rbac_denied` 日志被记录，**level=info**（**不是 warning**）
- **AND** 进程级 `_DISK_CHECK_DISABLED` 被置为 True
- **AND** 返回结果 `disk_pressure=False`

#### Scenario: ORCHN-S5 disk-check 已禁用后 gc_once 不再调 list_node

- **GIVEN** 上一轮 GC 已把 `_DISK_CHECK_DISABLED` 置为 True
- **WHEN** `gc_once()` 再次被调用
- **THEN** `node_disk_usage_ratio` 不被调用
- **AND** 没有任何 `runner_gc.disk_check_*` 日志被记录（INFO/WARNING/DEBUG 任一级别都不打）
- **AND** 返回结果 `disk_pressure=False`

#### Scenario: ORCHN-S6 非 403 异常仍走 debug 不禁用

- **GIVEN** `node_disk_usage_ratio` 抛出 `ApiException(status=500)`
- **WHEN** `gc_once()` 被调用
- **THEN** `log.debug("runner_gc.disk_check_failed", ...)` 被记录
- **AND** `_DISK_CHECK_DISABLED` 保持 False（下一轮仍会再尝试）
- **AND** 不发出任何 `runner_gc.disk_check_rbac_denied` info 或 warning 日志

#### Scenario: ORCHN-S7 disk-check 正常 ratio > threshold 时仍能触发紧急清理

- **GIVEN** RBAC 健全（list_node 200 OK），`ratio=0.9`，threshold=0.8
- **WHEN** `gc_once()` 被调用
- **THEN** `runner_gc.disk_pressure` warning 被记录
- **AND** 返回结果 `disk_pressure=True`

#### Scenario: ORCHN-S8 alert 看板按 level=warning 过滤时看不到 rbac_denied

- **GIVEN** 一次或多次 orchestrator 进程启动 + RBAC 缺 nodes:list
- **WHEN** alert / loki 等观测系统按 `level=warning` 查询
- **THEN** `runner_gc.disk_check_rbac_denied` 不出现在结果中（因为它现在是 info 级别）
