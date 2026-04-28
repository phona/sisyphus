## ADDED Requirements

### Requirement: start_challenger 必须在创建 BKD issue 后把 challenger_issue_id 写入 REQ context

The `start_challenger` action MUST call `req_state.update_context(pool, req_id, {"challenger_issue_id": issue.id})` after `bkd.update_issue(status_id="working")` completes and before the function returns. The call MUST use a connection pool obtained from `db.get_pool()`. This ensures that when the REQ transitions to `CHALLENGER_RUNNING` and the watchdog begins scanning, the `challenger_issue_id` key is already present in the REQ context so watchdog can query the BKD session status.

#### Scenario: SACP-S1 start_challenger 写 challenger_issue_id 到 ctx

- **GIVEN** REQ 进入 start_challenger，body.issueId="spec-lint-1"，req_id="REQ-CH"
- **WHEN** BKD create_issue 成功返回 issue.id="ch-new-1"，start_challenger 完成
- **THEN** `req_state.update_context` 被调用一次，参数 `(pool, "REQ-CH", {"challenger_issue_id": "ch-new-1"})`

#### Scenario: SACP-S2 start_challenger 返回值含 challenger_issue_id

- **GIVEN** REQ 进入 start_challenger，BKD 返回 issue.id="ch-42"
- **WHEN** start_challenger 完成
- **THEN** 返回 dict 包含 `{"challenger_issue_id": "ch-42", "req_id": ...}`

### Requirement: watchdog 必须在 _STATE_ISSUE_KEY 中包含 CHALLENGER_RUNNING

The `watchdog._STATE_ISSUE_KEY` dict MUST contain the entry `ReqState.CHALLENGER_RUNNING: "challenger_issue_id"`. This enables watchdog to look up the correct ctx key for the challenger stage and query BKD session status before deciding to escalate.

#### Scenario: SACP-S3 _STATE_ISSUE_KEY 包含 CHALLENGER_RUNNING 映射

- **GIVEN** orchestrator 启动
- **WHEN** 读取 `watchdog._STATE_ISSUE_KEY`
- **THEN** `_STATE_ISSUE_KEY[ReqState.CHALLENGER_RUNNING] == "challenger_issue_id"`

### Requirement: watchdog 必须在 CHALLENGER_RUNNING + issue_id 缺失时跳过而非 escalate

The `watchdog._check_and_escalate` function MUST, when `state == ReqState.CHALLENGER_RUNNING` and `issue_id is None`, log a warning and return `False` (skip escalation) rather than proceeding to the escalate path. This is a defense-in-depth guard: a missing `challenger_issue_id` in ctx should be treated as a transient condition (ctx write in flight or pre-fix deployment), not as evidence that the challenger session has ended. This guard MUST be scoped only to `CHALLENGER_RUNNING`; it MUST NOT apply to other states such as `FIXER_RUNNING` that have hard safety caps which must fire regardless of issue_id presence.

#### Scenario: SACP-S4 CHALLENGER_RUNNING + ctx 无 challenger_issue_id → 跳过

- **GIVEN** REQ row state=CHALLENGER_RUNNING，ctx 不含 `challenger_issue_id`（issue_id=None），卡死时间超阈值
- **WHEN** watchdog._check_and_escalate 跑
- **THEN** 返回 False；`engine.step` 不被调；log `watchdog.missing_issue_id` warning 被发出

#### Scenario: SACP-S5 CHALLENGER_RUNNING + issue_id in ctx + session running → 跳过

- **GIVEN** REQ row state=CHALLENGER_RUNNING，ctx.challenger_issue_id="ch-99"，BKD session_status="running"，卡死时间超阈值
- **WHEN** watchdog._check_and_escalate 跑
- **THEN** 返回 False；`engine.step` 不被调（agent 仍在运行，不误 escalate）

#### Scenario: SACP-S6 CHALLENGER_RUNNING + issue_id in ctx + session failed → escalate

- **GIVEN** REQ row state=CHALLENGER_RUNNING，ctx.challenger_issue_id="ch-99"，BKD session_status="failed"，卡死时间超阈值
- **WHEN** watchdog._check_and_escalate 跑
- **THEN** 返回 True；`engine.step` 被调，event=SESSION_FAILED

#### Scenario: SACP-S7 其他 state（如 STAGING_TEST_RUNNING）+ issue_id 缺失 → 照常 escalate

- **GIVEN** REQ row state=STAGING_TEST_RUNNING，ctx 不含 staging_test_issue_id（issue_id=None），卡死时间超阈值
- **WHEN** watchdog._check_and_escalate 跑
- **THEN** CHALLENGER_RUNNING 守卫不触发；engine.step 被调，照常 escalate（现有行为不变）
