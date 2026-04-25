## ADDED Requirements

### Requirement: start_fixer 在每次起 fixer 之前必须自检 fixer round cap

The `start_fixer` action MUST evaluate `next_round = (ctx.fixer_round or 0) + 1` against `settings.fixer_round_cap` (default 5) before invoking BKD to create a new fixer issue. When `next_round > cap`, the action MUST NOT create a fixer issue, MUST persist `escalated_reason="fixer-round-cap"` and `fixer_round_cap_hit=cap` to the REQ context, and MUST return `{"emit": "verify.escalate"}` so that the engine drives the standard escalate path. When `next_round <= cap`, the action MUST persist `fixer_round = next_round` to context and tag the new fixer issue with `round:N`.

#### Scenario: FRC-S1 第一次 start_fixer 写 round=1

- **GIVEN** REQ context 不含 `fixer_round` 字段（fresh REQ）
- **WHEN** start_fixer 被调度
- **THEN** ctx.fixer_round = 1，BKD create_issue 调用一次，issue tags 含 `round:1`，bugfix prompt 渲染 `ROUND=1`

#### Scenario: FRC-S2 round 计数随每轮单调递增

- **GIVEN** REQ context.fixer_round = 2（已起过 2 轮）
- **WHEN** start_fixer 被调度（verifier 第三次判 fix）
- **THEN** ctx.fixer_round 更新为 3，新 issue tag 含 `round:3`，bugfix prompt 渲 `ROUND=3`

#### Scenario: FRC-S3 第 (cap+1) 次 start_fixer 触发 escalate

- **GIVEN** REQ context.fixer_round = 5，settings.fixer_round_cap = 5
- **WHEN** start_fixer 被调度
- **THEN** BKD create_issue 不被调用；ctx.escalated_reason = "fixer-round-cap"，ctx.fixer_round_cap_hit = 5；返回 `{"emit": "verify.escalate", "reason": "fixer-round-cap", "fixer_round": 5, "cap": 5}`

#### Scenario: FRC-S4 cap 可通过 settings 覆盖

- **GIVEN** settings.fixer_round_cap = 2 + REQ context.fixer_round = 2
- **WHEN** start_fixer 被调度
- **THEN** 触发 cap escalate（next_round=3 > cap=2），不创 fixer issue

### Requirement: 状态机必须支持 FIXER_RUNNING 在 VERIFY_ESCALATE 事件下推进到 ESCALATED

The state machine MUST define a transition `(FIXER_RUNNING, VERIFY_ESCALATE) → (ESCALATED, action="escalate")`. This transition allows `start_fixer` to chained-emit `verify.escalate` when the cap is hit, reusing the standard `escalate` action for reason persistence, intent issue tagging, and runner cleanup. The transition MUST coexist with the existing `(REVIEW_RUNNING, VERIFY_ESCALATE) → ESCALATED` transition without behavior change to that path.

#### Scenario: FRC-S5 FIXER_RUNNING + VERIFY_ESCALATE 推到 ESCALATED

- **GIVEN** state machine 当前 state=FIXER_RUNNING
- **WHEN** Event.VERIFY_ESCALATE 被 dispatch
- **THEN** decide() 返回非 None Transition：next_state=ESCALATED，action="escalate"

### Requirement: escalate.py 必须把 fixer-round-cap 作为 hard reason

The `escalate` action MUST treat `"fixer-round-cap"` as a hard reason: when present in `ctx.escalated_reason`, it MUST be preserved as the final escalate reason even if `body.event` is in `_CANONICAL_SIGNALS` (e.g., `watchdog.stuck`, `session.failed`). The `_is_transient` helper MUST return False whenever `reason == "fixer-round-cap"`, regardless of `body_event`. This MUST prevent the escalate path from auto-resuming the BKD session via follow-up "continue", which would otherwise restart the verifier↔fixer loop after the cap was tripped.

#### Scenario: FRC-S6 ctx hard reason 压过 canonical body.event

- **GIVEN** body.event="watchdog.stuck"，ctx.escalated_reason="fixer-round-cap"
- **WHEN** escalate action 跑
- **THEN** 输出 reason="fixer-round-cap"（不被 watchdog-stuck 覆盖）；不调 BKD follow_up_issue（不 auto-resume）；调 BKD merge_tags_and_update 加 tag `escalated` + `reason:fixer-round-cap`

#### Scenario: FRC-S7 _is_transient 对 fixer-round-cap 永远返 False

- **GIVEN** `_is_transient` 单测调用
- **WHEN** reason="fixer-round-cap"，body_event 取 "session.failed" / "watchdog.stuck" / None
- **THEN** 全返 False

### Requirement: watchdog 必须在 FIXER_RUNNING 卡死且 round 已达 cap 时标 reason

The `watchdog._check_and_escalate` function MUST, before emitting `Event.SESSION_FAILED`, check if `state == ReqState.FIXER_RUNNING` and `int(ctx.get("fixer_round") or 0) >= settings.fixer_round_cap`. When both conditions hold, the watchdog MUST persist `escalated_reason="fixer-round-cap"` and `fixer_round_cap_hit=cap` to the REQ context (and update the in-memory ctx so downstream `engine.step` sees it). This defense-in-depth path covers orphan FIXER_RUNNING REQs left behind when start_fixer wrote ctx but its emit failed mid-flight.

#### Scenario: FRC-S8 watchdog 兜底标 reason

- **GIVEN** REQ row state=FIXER_RUNNING + ctx.fixer_round=5 + cap=5 + 卡死时间超阈值
- **WHEN** watchdog._tick 跑
- **THEN** req_state.update_context 被调，写入 escalated_reason="fixer-round-cap"；engine.step 被调，event=SESSION_FAILED

#### Scenario: FRC-S9 watchdog 在 round<cap 时不写 fixer-round-cap

- **GIVEN** REQ row state=FIXER_RUNNING + ctx.fixer_round=2 + cap=5 + 卡死时间超阈值
- **WHEN** watchdog._tick 跑
- **THEN** req_state.update_context 没写入 escalated_reason="fixer-round-cap"（走原 watchdog-stuck 路径）
