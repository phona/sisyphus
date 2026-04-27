## ADDED Requirements

### Requirement: Engine routes intake-phase transitions through engine.step

The engine MUST dispatch the four intake-phase transitions through
`engine.step` exactly as `state.TRANSITIONS` declares: `(INIT,
INTENT_INTAKE) → INTAKING` via `start_intake`; `(INTAKING, INTAKE_PASS)
→ ANALYZING` via `start_analyze_with_finalized_intent`; `(INTAKING,
INTAKE_FAIL) → ESCALATED` via `escalate`; and `(INTAKING,
VERIFY_ESCALATE) → ESCALATED` via `escalate`. The two transitions that
land in `ESCALATED` MUST also fire-and-forget a `cleanup_runner` task
with `retain_pvc=True` because they cross from a non-terminal state
into a terminal one. A regression that drops or renames any of these
transitions MUST be caught by mock tests; intake is a human-in-loop
stage and a missed transition silently strands the REQ before any work
gets done.

#### Scenario: ERT-S1 init intent_intake enters intaking

- **GIVEN** a row at state `INIT` and a stub `start_intake` registered
  in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=INTENT_INTAKE`
- **THEN** the row's state MUST advance to `INTAKING`, the returned
  dict MUST contain `action="start_intake"` and `next_state="intaking"`,
  and the stub action MUST be awaited exactly once

#### Scenario: ERT-S2 intaking intake_pass enters analyzing

- **GIVEN** a row at state `INTAKING` and a stub
  `start_analyze_with_finalized_intent` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=INTAKE_PASS`
- **THEN** the row's state MUST advance to `ANALYZING`, the returned
  dict MUST contain `action="start_analyze_with_finalized_intent"` and
  `next_state="analyzing"`, and the stub action MUST be awaited exactly
  once

#### Scenario: ERT-S3 intaking intake_fail enters escalated and triggers cleanup

- **GIVEN** a row at state `INTAKING`, a stub `escalate` registered in
  `actions.REGISTRY`, and a fake k8s controller injected via
  `k8s_runner.set_controller`
- **WHEN** `engine.step` is called with `event=INTAKE_FAIL`
- **THEN** the row's state MUST advance to `ESCALATED`, the returned
  dict MUST contain `action="escalate"`, the stub action MUST be
  awaited exactly once, and after fire-and-forget tasks drain the fake
  controller's `cleanup_runner` MUST have been awaited exactly once
  with `retain_pvc=True`

#### Scenario: ERT-S4 intaking verify_escalate enters escalated and triggers cleanup

- **GIVEN** a row at state `INTAKING`, a stub `escalate` registered in
  `actions.REGISTRY`, and a fake k8s controller injected via
  `k8s_runner.set_controller`
- **WHEN** `engine.step` is called with `event=VERIFY_ESCALATE`
- **THEN** the row's state MUST advance to `ESCALATED`, the returned
  dict MUST contain `action="escalate"`, the stub action MUST be
  awaited exactly once, and after fire-and-forget tasks drain the fake
  controller's `cleanup_runner` MUST have been awaited exactly once
  with `retain_pvc=True`

### Requirement: Engine routes analyze-phase escalation through engine.step

The engine MUST dispatch `(ANALYZING, VERIFY_ESCALATE)` and `(PR_CI_RUNNING,
PR_CI_TIMEOUT)` to `ESCALATED` via the `escalate` action through
`engine.step`. These two transitions are reached when an analyze action
internally emits `VERIFY_ESCALATE` (clone failure, missing finalized
intent) or when the pr-ci checker times out waiting for GitHub check-runs.
Both MUST trigger a fire-and-forget `cleanup_runner(retain_pvc=True)`
because they cross into a terminal state.

#### Scenario: ERT-S5 analyzing verify_escalate enters escalated and triggers cleanup

- **GIVEN** a row at state `ANALYZING`, a stub `escalate` registered in
  `actions.REGISTRY`, and a fake k8s controller injected via
  `k8s_runner.set_controller`
- **WHEN** `engine.step` is called with `event=VERIFY_ESCALATE`
- **THEN** the row's state MUST advance to `ESCALATED`, the returned
  dict MUST contain `action="escalate"`, and after fire-and-forget
  tasks drain the fake controller's `cleanup_runner` MUST have been
  awaited exactly once with `retain_pvc=True`

#### Scenario: ERT-S6 pr_ci timeout enters escalated and triggers cleanup

- **GIVEN** a row at state `PR_CI_RUNNING`, a stub `escalate`
  registered in `actions.REGISTRY`, and a fake k8s controller injected
  via `k8s_runner.set_controller`
- **WHEN** `engine.step` is called with `event=PR_CI_TIMEOUT`
- **THEN** the row's state MUST advance to `ESCALATED`, the returned
  dict MUST contain `action="escalate"`, and after fire-and-forget
  tasks drain the fake controller's `cleanup_runner` MUST have been
  awaited exactly once with `retain_pvc=True`

### Requirement: Engine completes ESCALATED resume end-to-end through chain emit

The engine MUST allow `apply_verify_pass` to resume a REQ from
`ESCALATED` end-to-end: the action internally CAS-advances from
`ESCALATED` to the appropriate `*_RUNNING` state and emits the
corresponding upstream pass event; the engine MUST then chain-dispatch
the next-stage action through the main-chain transition. This proves
the human-driven follow-up path (BKD UI follow-up on an escalated
verifier issue → new `decision.action=pass` → engine resume)
end-to-end, beyond the dispatch-only surface that VLT-S12 covers.

The engine MUST also forward the `verify:<stage>` tag and ctx
`verifier_stage` through to the `start_fixer` handler when resuming
from `ESCALATED` via `VERIFY_FIX_NEEDED`, because `start_fixer` reads
the stage from the issue tag (multi-verifier-concurrent ctx race).

#### Scenario: ERT-S7 escalated verify_pass chains to next stage end-to-end

- **GIVEN** a row at state `ESCALATED`, a stub `apply_verify_pass`
  registered in `actions.REGISTRY` that internally CAS-advances the
  pool row from `ESCALATED` to `STAGING_TEST_RUNNING` and returns
  `{"emit": "staging-test.pass"}`, and a stub `create_pr_ci_watch`
  registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=VERIFY_PASS` and
  `tags=["verifier", "REQ-1", "verify:staging_test"]`
- **THEN** the returned dict MUST contain `action="apply_verify_pass"`
  with a `chained` sub-result whose `action` is `"create_pr_ci_watch"`
  and whose `next_state` is `"pr-ci-running"`, the row's state MUST
  end at `PR_CI_RUNNING`, and both stub actions MUST be awaited
  exactly once

#### Scenario: ERT-S8 escalated verify_fix_needed forwards stage tag

- **GIVEN** a row at state `ESCALATED` and a stub `start_fixer`
  registered in `actions.REGISTRY` that records the `tags` and `ctx`
  it receives
- **WHEN** `engine.step` is called with `event=VERIFY_FIX_NEEDED`,
  `tags=["verifier", "REQ-1", "verify:staging_test"]`, and
  `ctx={"verifier_stage": "staging_test", "verifier_fixer": "dev"}`
- **THEN** the row's state MUST advance to `FIXER_RUNNING`, the
  returned dict MUST contain `action="start_fixer"`, the stub MUST be
  awaited exactly once, and the recorded `tags` MUST contain
  `"verify:staging_test"` and the recorded `ctx` MUST contain
  `verifier_stage="staging_test"`

### Requirement: Engine.step routes every declared transition correctly (47/47 sweep)

The engine MUST honor every entry in `state.TRANSITIONS` when invoked
through `engine.step`: for each `(state, event)` key the engine MUST
either dispatch the declared `action` (when non-None) or return
`action="no-op"` (when the transition's action is None), and the row's
state MUST advance to the declared `next_state`. This is the
defense-in-depth catch-all that fires if any granular per-transition
test (in `test_engine_main_chain.py`, `test_engine_accept_phase.py`,
`test_engine_verifier_loop.py`, or this file) drifts away from the
transition table.

#### Scenario: ERT-S9 every TRANSITIONS entry round-trips through engine.step

- **GIVEN** the static `state.TRANSITIONS` mapping containing all
  declared `(state, event) → Transition` rows, and a generic stub
  registered in `actions.REGISTRY` for every `transition.action` value
  encountered
- **WHEN** `engine.step` is called once per `(state, event)` row with
  `cur_state=state` and `event=event`
- **THEN** for every iteration the returned dict MUST contain
  `action == transition.action` (or `action == "no-op"` when
  `transition.action is None`), `next_state == transition.next_state.value`,
  and the pool row state MUST equal `transition.next_state.value` after
  the step
