## ADDED Requirements

### Requirement: 状态机调度 escalate action 前必须预填 ctx.escalated_reason

The orchestrator engine SHALL pre-populate `ctx.escalated_reason` with a canonical, event-specific reason slug before dispatching the `escalate` action when the triggering state-machine `Event` is one of `INTAKE_FAIL`, `PR_CI_TIMEOUT`, `ACCEPT_ENV_UP_FAIL`, or `VERIFY_ESCALATE`. The system MUST persist the pre-populated reason via `req_state.update_context` so it survives action retries and subsequent reads from `failure_mode` and other observability views.

The reason slug MUST be deterministic per `Event`:

- `Event.INTAKE_FAIL` → `"intake-fail"`
- `Event.PR_CI_TIMEOUT` → `"pr-ci-timeout"`
- `Event.ACCEPT_ENV_UP_FAIL` → `"accept-env-up-fail"`
- `Event.VERIFY_ESCALATE` → `"verifier-decision-escalate"`

The system SHALL NOT pre-populate `ctx.escalated_reason` when the triggering Event is `SESSION_FAILED`, because `actions/escalate.py` already derives the correct reason from `body.event` (`session.failed` / `watchdog.stuck`) for that path.

The system SHALL NOT overwrite an existing `ctx.escalated_reason` value that starts with `"action-error:"`, because such values are written by `engine._emit_escalate` to carry handler-exception context and are strictly more informative than the generic Event slug.

#### Scenario: ESC-S1 INTAKE_FAIL 经 engine.step 后 ctx.escalated_reason 被预填

- **GIVEN** REQ 处于 `ReqState.INTAKING` 且 `ctx.escalated_reason` 不存在
- **WHEN** `engine.step` 收到 `Event.INTAKE_FAIL`，触发 transition action `escalate`
- **THEN** `escalate` action 接收到的 ctx 中 `escalated_reason` 已是 `"intake-fail"`，且 DB 中持久化了同样的值

#### Scenario: ESC-S2 PR_CI_TIMEOUT 经 engine.step 后 ctx.escalated_reason 被预填

- **GIVEN** REQ 处于 `ReqState.PR_CI_RUNNING` 且 `ctx.escalated_reason` 不存在
- **WHEN** `engine.step` 收到 `Event.PR_CI_TIMEOUT`，触发 transition action `escalate`
- **THEN** `escalate` action 接收到的 ctx 中 `escalated_reason` 已是 `"pr-ci-timeout"`，且 DB 中持久化了同样的值

#### Scenario: ESC-S3 ACCEPT_ENV_UP_FAIL 经 engine.step 后 ctx.escalated_reason 被预填

- **GIVEN** REQ 处于 `ReqState.ACCEPT_RUNNING` 且 `ctx.escalated_reason` 不存在
- **WHEN** `engine.step` 收到 `Event.ACCEPT_ENV_UP_FAIL`，触发 transition action `escalate`
- **THEN** `escalate` action 接收到的 ctx 中 `escalated_reason` 已是 `"accept-env-up-fail"`，且 DB 中持久化了同样的值

#### Scenario: ESC-S4 VERIFY_ESCALATE 经 engine.step 后 ctx.escalated_reason 被预填

- **GIVEN** REQ 处于 `ReqState.REVIEW_RUNNING` 且 `ctx.escalated_reason` 不存在
- **WHEN** `engine.step` 收到 `Event.VERIFY_ESCALATE`，触发 transition action `escalate`
- **THEN** `escalate` action 接收到的 ctx 中 `escalated_reason` 已是 `"verifier-decision-escalate"`，且 DB 中持久化了同样的值

#### Scenario: ESC-S5 SESSION_FAILED 走 engine.step 不预填 escalated_reason

- **GIVEN** REQ 处于任意 `*_RUNNING` 状态，`body.event = "session.failed"`，`ctx.escalated_reason` 不存在
- **WHEN** `engine.step` 收到 `Event.SESSION_FAILED`，触发 transition action `escalate`
- **THEN** `engine` 不向 ctx 写入 `escalated_reason`（保留 escalate.py 的 canonical body.event 处理路径），最终 `escalate` action 内部把 `ctx.escalated_reason` 设为 `"session-failed"`

#### Scenario: ESC-S6 action-error:... 已存在时 engine 不覆盖

- **GIVEN** `ctx.escalated_reason` 已是 `"action-error:RuntimeError: pod not ready"`（`_emit_escalate` 写入），随后递归 `engine.step` 收到 `Event.SESSION_FAILED`
- **WHEN** engine 在 dispatch escalate 前评估是否预填
- **THEN** engine 不修改 `ctx.escalated_reason`，保留原 `action-error:...` 值给 `escalate.py` 的 `_is_transient` 判 transient

#### Scenario: ESC-S7 escalate.py 真 escalate 写入的 final_reason 与预填一致

- **GIVEN** ctx.escalated_reason 已被 engine 预填为 `"verifier-decision-escalate"`，`auto_retry_count=0`，`body.event="session.completed"`
- **WHEN** `escalate` action 跑到 real-escalate 分支
- **THEN** `final_reason == "verifier-decision-escalate"`；BKD intent issue 加 tag `reason:verifier-decision-escalate`；`ctx.escalated_reason` 持久化为 `"verifier-decision-escalate"`
