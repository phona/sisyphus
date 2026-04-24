## ADDED Requirements

### Requirement: alerts 表 + 统一告警写入路径

The system SHALL create an `alerts` PostgreSQL table with columns: id (BIGSERIAL PK), severity (TEXT NOT NULL CHECK IN ('info','warn','critical')), req_id (TEXT), stage (TEXT), reason (TEXT NOT NULL), hint (TEXT), suggested_action (TEXT), created_at (TIMESTAMPTZ DEFAULT NOW()), acknowledged_at (TIMESTAMPTZ), sent_to_tg (BOOLEAN DEFAULT FALSE). The system MUST enforce the severity constraint at the database level. A partial index on `(created_at DESC) WHERE acknowledged_at IS NULL` MUST exist for dashboard queries.

The system SHALL provide `store/alerts.insert_alert(pool, *, severity, reason, ...)` returning the inserted row id, and `alerts.insert(*, severity, reason, ...)` as a convenience wrapper that auto-obtains the pool via `db.get_pool()`.

#### Scenario: ALERTS-S1 insert_alert 插入返回 id

- **GIVEN** pool 可用，severity='critical', reason='watchdog-stuck-30min'
- **WHEN** 调用 `insert_alert(pool, severity='critical', reason='watchdog-stuck-30min', req_id='REQ-1')`
- **THEN** 返回 int id > 0，alerts 表中新增一行

#### Scenario: ALERTS-S2 severity CHECK 约束拒绝非法值

- **GIVEN** severity='invalid'
- **WHEN** 调用 `insert_alert(pool, severity='invalid', reason='x')`
- **THEN** 抛出 IntegrityConstraintViolationError（PostgreSQL CHECK constraint）

### Requirement: Telegram Bot 静默推送

The system SHALL implement `alerts.tg.send_critical(text: str) -> bool` that sends a message via Telegram Bot API using `settings.tg_bot_token` and `settings.tg_chat_id`. The function MUST return False without raising an exception if either setting is missing or if the HTTP request fails.

#### Scenario: ALERTS-S3 无 TG 配置时静默返回 False

- **GIVEN** `settings.tg_bot_token` 为 None
- **WHEN** 调用 `send_critical("test")`
- **THEN** 返回 False，不发起 HTTP 请求，不抛异常

#### Scenario: ALERTS-S4 HTTP 200 时返回 True

- **GIVEN** settings 配置了有效 token/chat_id，HTTP mock 返回 200
- **WHEN** 调用 `send_critical("test message")`
- **THEN** 返回 True

#### Scenario: ALERTS-S5 网络异常时静默返回 False

- **GIVEN** HTTP 请求抛出 ConnectError
- **WHEN** 调用 `send_critical("test")`
- **THEN** 返回 False，不抛异常

### Requirement: escalate reason 精化与告警写入

The system SHALL update `escalate.py` so that `ctx.escalated_reason` takes priority over the event name when determining the escalation reason. The escalation action MUST write a record to the `alerts` table with severity='critical' and MUST attempt to send a Telegram notification via `tg.send_critical`.

#### Scenario: ALERTS-S6 ctx.escalated_reason 优先于 body.event

- **GIVEN** ctx 含 `escalated_reason="runner-pod-not-ready"`, body.event="SESSION_FAILED"
- **WHEN** `escalate` action 执行
- **THEN** alerts 表中 reason="runner-pod-not-ready"，而非 "session-failed"

#### Scenario: ALERTS-S7 escalate 写 alert + 推 TG

- **GIVEN** 正常 escalate 触发
- **WHEN** `escalate` action 执行
- **THEN** `alerts.insert` 被调用一次，`tg.send_critical` 被调用一次

### Requirement: K8s pod 启动诊断

The system SHALL add `_diagnose_pod(pod_name: str) -> str` to `K8sRunnerController` that queries the K8s events API for the given pod and returns a human-readable diagnosis string. The method MUST identify at least: ImagePullBackOff/ErrImagePull → "image pull failed"; WaitForFirstConsumer/WaitForPodScheduled → "PVC pending (likely disk pressure or scheduler can't fit)"; Insufficient resources → "node resource insufficient". On API failure the method MUST return "diagnostic failed" without raising.

The system SHALL add `delete_pvc(req_id: str) -> bool` method that deletes the PVC named `runner-<req_id>` in the runners namespace.

#### Scenario: ALERTS-S8 ImagePullBackOff → "image pull failed"

- **GIVEN** K8s events 包含 reason=ImagePullBackOff
- **WHEN** `_diagnose_pod("pod-x")` 被调用
- **THEN** 返回 "image pull failed"

#### Scenario: ALERTS-S9 WaitForFirstConsumer → PVC pending

- **GIVEN** K8s events 包含 message 含 "WaitForFirstConsumer"
- **WHEN** `_diagnose_pod("pod-x")` 被调用
- **THEN** 返回 "PVC pending (likely disk pressure or scheduler can't fit)"

#### Scenario: ALERTS-S10 API 失败 → "diagnostic failed"

- **GIVEN** `core_v1.list_namespaced_event` 抛出异常
- **WHEN** `_diagnose_pod("pod-x")` 被调用
- **THEN** 返回 "diagnostic failed"，不抛异常

### Requirement: runner 超时时写入诊断上下文

The system SHALL modify `apply_verify_pass` to catch `TimeoutError` from `ensure_runner`, call `_diagnose_pod`, update `ctx.escalated_reason="runner-pod-not-ready"` and `ctx.escalated_hint=<diagnosis>` via `req_state.update_context`, then re-raise the exception so the engine handles it via the SESSION_FAILED path.

### Requirement: watchdog 两阶段告警

The system SHALL use `_WARN_THRESHOLD_SEC = 300` (5 min) as the SQL fetch threshold. For rows stuck 5–30 minutes that have not yet been warned, the system MUST insert an `alert(severity='warn', reason='stuck-5min')` and set `ctx.warned_at_5min=True`. For rows stuck ≥30 minutes, the system MUST escalate via `engine.step` with `ctx.escalated_reason="watchdog-stuck-30min"`. The `_tick()` function MUST return `{"checked": N, "escalated": N, "warned": N}`.

#### Scenario: ALERTS-S11 5min warn 写 alert + ctx，不 escalate

- **GIVEN** stuck_sec=600（5–30min 区间），warned_at_5min 未设
- **WHEN** `_tick()` 运行
- **THEN** result["warned"]=1, result["escalated"]=0; alerts 写了 severity='warn', reason='stuck-5min'; ctx.warned_at_5min=True

#### Scenario: ALERTS-S12 warned_at_5min 已设 → 不重复告警

- **GIVEN** stuck_sec=700，ctx.warned_at_5min=True
- **WHEN** `_tick()` 运行
- **THEN** result["warned"]=0，alert_calls=[]

#### Scenario: ALERTS-S13 30min escalate 写 ctx.escalated_reason

- **GIVEN** stuck_sec=2000（≥30min）
- **WHEN** `_tick()` 运行
- **THEN** result["escalated"]=1; ctx.escalated_reason="watchdog-stuck-30min"

### Requirement: fixer 循环检测

The system SHALL detect fixer loops in `invoke_verifier_after_fix`. After appending the current fixer round to `ctx.verifier_history`, if `len(history) > 3` the system MUST write `ctx.escalated_reason="fixer-loop-3rounds"` and return `{"emit": Event.VERIFY_ESCALATE.value}` instead of launching another verifier. An `alert(severity='critical', reason='fixer-loop-3rounds')` MUST also be inserted.

#### Scenario: ALERTS-S14 3+1 轮 → VERIFY_ESCALATE

- **GIVEN** `ctx.verifier_history` 已有 3 条记录，当前为第 4 次
- **WHEN** `invoke_verifier_after_fix` 执行
- **THEN** 返回 `{"emit": "VERIFY_ESCALATE", ...}`，alert 写了 reason='fixer-loop-3rounds'

#### Scenario: ALERTS-S15 2+1=3 轮 → 正常启动 verifier

- **GIVEN** `ctx.verifier_history` 已有 2 条记录，当前为第 3 次
- **WHEN** `invoke_verifier_after_fix` 执行
- **THEN** 启动 verifier（不返回 VERIFY_ESCALATE）

### Requirement: PVC GC

The system SHALL provide `runner_gc.gc_pvcs() -> dict` that independently manages PVC lifecycle: done→immediate delete; escalated→retain 24h then delete; disk usage >80%→force purge non-active PVCs. Active req_ids MUST NOT have their PVCs deleted even under disk pressure. The `_disk_pressure()` helper MUST use `shutil.disk_usage('/')` returning a float 0.0–1.0.

#### Scenario: ALERTS-S16 state=done → 立即删 PVC

- **GIVEN** state='done'，updated_at=now
- **WHEN** `gc_pvcs()` 运行
- **THEN** `delete_pvc("REQ-D1")` 被调用一次

#### Scenario: ALERTS-S17 state=escalated < 24h → 不删

- **GIVEN** state='escalated'，updated_at=12h 前
- **WHEN** `gc_pvcs()` 运行
- **THEN** `delete_pvc` 未被调用

#### Scenario: ALERTS-S18 state=escalated > 24h → 删

- **GIVEN** state='escalated'，updated_at=25h 前
- **WHEN** `gc_pvcs()` 运行
- **THEN** `delete_pvc` 被调用

#### Scenario: ALERTS-S19 disk pressure > 80% 强清非 active

- **GIVEN** disk usage=85%，escalated PVC 未过 24h 但 req 非 active
- **WHEN** `gc_pvcs()` 运行
- **THEN** `delete_pvc` 被调用

#### Scenario: ALERTS-S20 disk pressure > 80% 但 req active → 不删

- **GIVEN** disk usage=90%，escalated PVC 的 req 仍 active（in-flight）
- **WHEN** `gc_pvcs()` 运行
- **THEN** `delete_pvc` 未被调用
