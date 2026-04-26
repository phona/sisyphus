## ADDED Requirements

### Requirement: 状态机必须支持 ANALYZING 在 VERIFY_ESCALATE 事件下推进到 ESCALATED

The state machine MUST define a transition `(ANALYZING, VERIFY_ESCALATE) → (ESCALATED, action="escalate")`. This transition allows the `start_analyze` and `start_analyze_with_finalized_intent` action handlers to chained-emit `verify.escalate` when the server-side `_clone` helper fails (non-zero exit code) or when `ctx.intake_finalized_intent` is missing, reusing the standard `escalate` action for reason persistence, intent-issue tagging, and runner cleanup. Without this transition, the engine MUST log `engine.illegal_transition` and the REQ row stays in `analyzing` until the watchdog escalates via `SESSION_FAILED`.

#### Scenario: CFE-S1 ANALYZING + VERIFY_ESCALATE 推到 ESCALATED

- **GIVEN** state machine 当前 state=ANALYZING
- **WHEN** Event.VERIFY_ESCALATE 被 dispatch
- **THEN** decide() 返回非 None Transition：next_state=ESCALATED，action="escalate"

#### Scenario: CFE-S2 start_analyze emit verify.escalate 完整链路推到 ESCALATED

- **GIVEN** REQ 当前 state=INIT，body.event="intent.analyze"，stub `start_analyze` 返回 `{"emit": "verify.escalate", "reason": "clone failed (rc=5)"}`，stub `escalate` 返回 `{"ok": True}`
- **WHEN** engine.step 处理 Event.INTENT_ANALYZE
- **THEN** action 调用顺序为 start_analyze, escalate；req_state.state=ESCALATED；step 返回 dict 中 `chained.action == "escalate"`，**不**出现 `engine.illegal_transition` log

#### Scenario: CFE-S3 start_analyze_with_finalized_intent emit verify.escalate 推到 ESCALATED

- **GIVEN** REQ 当前 state=INTAKING，body.event="session.completed"，stub `start_analyze_with_finalized_intent` 返回 `{"emit": "verify.escalate", "reason": "intake_finalized_intent missing in ctx"}`，stub `escalate` 返回 `{"ok": True}`
- **WHEN** engine.step 处理 Event.INTAKE_PASS
- **THEN** action 调用顺序为 start_analyze_with_finalized_intent, escalate；req_state.state=ESCALATED；chain 不报 illegal_transition

### Requirement: 状态机必须支持 INTAKING 在 VERIFY_ESCALATE 事件下推进到 ESCALATED

The state machine MUST define a transition `(INTAKING, VERIFY_ESCALATE) → (ESCALATED, action="escalate")`. This is a defensive symmetry counterpart to the `ANALYZING` transition: although the current `start_intake` action does not emit `verify.escalate`, future evolution where intake-stage actions perform server-side clones or other failable bootstrap work MUST be able to emit `verify.escalate` and rely on the engine to drive the standard escalate path rather than silently logging `engine.illegal_transition`. The transition MUST coexist with `(INTAKING, INTAKE_PASS)` and `(INTAKING, INTAKE_FAIL)` without behavior change.

#### Scenario: CFE-S4 INTAKING + VERIFY_ESCALATE 推到 ESCALATED

- **GIVEN** state machine 当前 state=INTAKING
- **WHEN** Event.VERIFY_ESCALATE 被 dispatch
- **THEN** decide() 返回非 None Transition：next_state=ESCALATED，action="escalate"

#### Scenario: CFE-S5 INTAKING 既有 transition 不被影响

- **GIVEN** state machine 当前 state=INTAKING
- **WHEN** Event.INTAKE_PASS / Event.INTAKE_FAIL 被 dispatch
- **THEN** decide() 仍返回原 Transition（ANALYZING + start_analyze_with_finalized_intent / ESCALATED + escalate），不被新加的 VERIFY_ESCALATE transition 干扰
