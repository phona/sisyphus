## ADDED Requirements

### Requirement: Engine routes upstream stage failures into verifier sub-chain

The state machine and engine MUST route every supported upstream stage-fail
event from its corresponding running state to `REVIEW_RUNNING` and dispatch
the matching `invoke_verifier_for_<stage>_fail` action. The supported stages
are analyze-artifact-check, spec-lint, challenger, dev-cross-check,
staging-test, pr-ci, and accept-teardown. A regression that drops or renames
any of these transitions MUST be caught by mock tests; the engine MUST NOT
silently skip a stage-fail event when it is fired from the matching running
state.

#### Scenario: VLT-S1 spec_lint fail enters review-running

- **GIVEN** a row at state `SPEC_LINT_RUNNING` and a stub
  `invoke_verifier_for_spec_lint_fail` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=SPEC_LINT_FAIL`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain `action="invoke_verifier_for_spec_lint_fail"` and
  `next_state="review-running"`, and the stub action MUST be awaited exactly
  once

#### Scenario: VLT-S2 dev_cross_check fail enters review-running

- **GIVEN** a row at state `DEV_CROSS_CHECK_RUNNING` and a stub
  `invoke_verifier_for_dev_cross_check_fail` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=DEV_CROSS_CHECK_FAIL`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain `action="invoke_verifier_for_dev_cross_check_fail"`, and
  the stub action MUST be awaited exactly once

#### Scenario: VLT-S3 staging_test fail enters review-running

- **GIVEN** a row at state `STAGING_TEST_RUNNING` and a stub
  `invoke_verifier_for_staging_test_fail` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=STAGING_TEST_FAIL`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain `action="invoke_verifier_for_staging_test_fail"`, and
  the stub action MUST be awaited exactly once

#### Scenario: VLT-S4 pr_ci fail enters review-running

- **GIVEN** a row at state `PR_CI_RUNNING` and a stub
  `invoke_verifier_for_pr_ci_fail` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=PR_CI_FAIL`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain `action="invoke_verifier_for_pr_ci_fail"`, and the stub
  action MUST be awaited exactly once

#### Scenario: VLT-S5 accept teardown fail enters review-running

- **GIVEN** a row at state `ACCEPT_TEARING_DOWN` and a stub
  `invoke_verifier_for_accept_fail` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=TEARDOWN_DONE_FAIL`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain `action="invoke_verifier_for_accept_fail"`, and the
  stub action MUST be awaited exactly once

#### Scenario: VLT-S6 analyze_artifact_check fail enters review-running

- **GIVEN** a row at state `ANALYZE_ARTIFACT_CHECKING` and a stub
  `invoke_verifier_for_analyze_artifact_check_fail` registered in
  `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=ANALYZE_ARTIFACT_CHECK_FAIL`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain
  `action="invoke_verifier_for_analyze_artifact_check_fail"`, and the stub
  action MUST be awaited exactly once

#### Scenario: VLT-S7 challenger fail enters review-running

- **GIVEN** a row at state `CHALLENGER_RUNNING` and a stub
  `invoke_verifier_for_challenger_fail` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=CHALLENGER_FAIL`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain `action="invoke_verifier_for_challenger_fail"`, and the
  stub action MUST be awaited exactly once

### Requirement: Engine routes verifier decisions out of review-running

The engine MUST dispatch the verifier-decision events from `REVIEW_RUNNING`
to the correct downstream state and action: `VERIFY_FIX_NEEDED` →
`FIXER_RUNNING` via `start_fixer`, and `VERIFY_ESCALATE` → `ESCALATED` via
`escalate`. The `VERIFY_FIX_NEEDED` path SHALL also close the `verifier`
stage_run row with `outcome=fix` and open a fresh `fixer` stage_run row,
because the transition crosses two distinct `*_RUNNING` states. The
`VERIFY_ESCALATE` path SHALL fire-and-forget a `cleanup_runner` task because
the transition enters a terminal state from a non-terminal state.

#### Scenario: VLT-S8 verify fix needed enters fixer-running and rolls stage_runs

- **GIVEN** a row at state `REVIEW_RUNNING` and a stub `start_fixer`
  registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=VERIFY_FIX_NEEDED`
- **THEN** the row's state MUST advance to `FIXER_RUNNING`, the returned
  dict MUST contain `action="start_fixer"`, exactly one stage_runs `close`
  call MUST record `(stage="verifier", outcome="fix")`, and exactly one
  stage_runs `insert` call MUST record `(stage="fixer")`

#### Scenario: VLT-S9 verify escalate enters escalated and triggers cleanup

- **GIVEN** a row at state `REVIEW_RUNNING`, a stub `escalate` registered
  in `actions.REGISTRY`, and a fake k8s controller injected via
  `k8s_runner.set_controller`
- **WHEN** `engine.step` is called with `event=VERIFY_ESCALATE`
- **THEN** the row's state MUST advance to `ESCALATED`, the returned dict
  MUST contain `action="escalate"`, and after fire-and-forget tasks drain
  the fake controller's `cleanup_runner` MUST have been awaited exactly
  once with `retain_pvc=True`

### Requirement: Engine handles fixer back-edge and round-cap escape

The engine MUST dispatch `(FIXER_RUNNING, FIXER_DONE)` to `REVIEW_RUNNING`
via `invoke_verifier_after_fix` so the post-fix re-verification loop runs
through the engine like the original verifier path. The engine MUST also
dispatch `(FIXER_RUNNING, VERIFY_ESCALATE)` to `ESCALATED` via `escalate`
because `start_fixer` itself can emit `VERIFY_ESCALATE` when the round cap
is hit and the engine needs a real transition to commit that decision.

#### Scenario: VLT-S10 fixer done re-enters review-running

- **GIVEN** a row at state `FIXER_RUNNING` and a stub
  `invoke_verifier_after_fix` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=FIXER_DONE`
- **THEN** the row's state MUST advance to `REVIEW_RUNNING`, the returned
  dict MUST contain `action="invoke_verifier_after_fix"`, and exactly one
  stage_runs `close` call MUST record `(stage="fixer", outcome="pass")`

#### Scenario: VLT-S11 fixer round-cap escapes to escalated

- **GIVEN** a row at state `FIXER_RUNNING` and a stub `escalate`
  registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=VERIFY_ESCALATE`
- **THEN** the row's state MUST advance to `ESCALATED`, the returned dict
  MUST contain `action="escalate"`, and the stub action MUST be awaited
  exactly once

### Requirement: Engine routes user-resume events from escalated state

The engine MUST allow user-driven resumption from `ESCALATED` by accepting
the three verifier-decision events fired from a follow-up `verifier`
issue: `VERIFY_PASS` is a self-loop dispatched to `apply_verify_pass`
(action internally CAS-advances to the appropriate `*_RUNNING` state),
`VERIFY_FIX_NEEDED` advances to `FIXER_RUNNING` via `start_fixer`, and
`VERIFY_ESCALATE` is a self-loop with `action=None` (no further action
because the row is already escalated). The `VERIFY_ESCALATE` self-loop
MUST NOT trigger a second `cleanup_runner` task — the prior escalate
already cleaned up.

#### Scenario: VLT-S12 escalated verify pass dispatches apply_verify_pass

- **GIVEN** a row at state `ESCALATED` and a stub `apply_verify_pass`
  registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=VERIFY_PASS`
- **THEN** the returned dict MUST contain `action="apply_verify_pass"`,
  the row's state MUST remain `ESCALATED` (transition table self-loop;
  the action stub does not internally CAS), and the stub action MUST be
  awaited exactly once

#### Scenario: VLT-S13 escalated verify fix needed enters fixer-running

- **GIVEN** a row at state `ESCALATED` and a stub `start_fixer`
  registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=VERIFY_FIX_NEEDED`
- **THEN** the row's state MUST advance to `FIXER_RUNNING`, the returned
  dict MUST contain `action="start_fixer"`, and the stub action MUST be
  awaited exactly once

#### Scenario: VLT-S14 escalated verify escalate is a no-op self-loop

- **GIVEN** a row at state `ESCALATED` and a fake k8s controller
  injected via `k8s_runner.set_controller`
- **WHEN** `engine.step` is called with `event=VERIFY_ESCALATE`
- **THEN** the returned dict MUST contain `action="no-op"` and
  `next_state="escalated"`, the row's state MUST remain `ESCALATED`,
  and the fake controller's `cleanup_runner` MUST NOT have been awaited

### Requirement: Engine routes SESSION_FAILED from every running state

The state machine MUST register `(state, SESSION_FAILED) → (state,
"escalate")` self-loop transitions for **every** non-terminal `*_RUNNING`
state and for `INTAKING` / `ARCHIVING`. When `engine.step` receives
`SESSION_FAILED` from any of those states it MUST dispatch the `escalate`
action with the current state preserved (the `escalate` action internally
decides auto-resume vs. real CAS to `ESCALATED`). States that are NOT in
the `SESSION_FAILED` transition set (`INIT`, `GH_INCIDENT_OPEN`, `DONE`,
`ESCALATED`) MUST cause `engine.step` to return `action="skip"` with a
`no transition` reason — `escalate` MUST NOT be dispatched.

#### Scenario: VLT-S15 SESSION_FAILED on every running state self-loops to escalate

- **GIVEN** a row at any of the 13 in-flight states (`INTAKING`,
  `ANALYZING`, `ANALYZE_ARTIFACT_CHECKING`, `SPEC_LINT_RUNNING`,
  `CHALLENGER_RUNNING`, `DEV_CROSS_CHECK_RUNNING`,
  `STAGING_TEST_RUNNING`, `PR_CI_RUNNING`, `ACCEPT_RUNNING`,
  `ACCEPT_TEARING_DOWN`, `REVIEW_RUNNING`, `FIXER_RUNNING`, `ARCHIVING`)
  and a stub `escalate` registered in `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=SESSION_FAILED`
- **THEN** the returned dict MUST contain `action="escalate"` and
  `next_state` equal to the same input state (transition table
  self-loop; action stub does not CAS), and the stub action MUST be
  awaited exactly once for each parametrized state

#### Scenario: VLT-S16 SESSION_FAILED on INIT state is dropped

- **GIVEN** a row at state `INIT` and a stub `escalate` registered in
  `actions.REGISTRY`
- **WHEN** `engine.step` is called with `event=SESSION_FAILED`
- **THEN** the returned dict MUST contain `action="skip"` and a `reason`
  containing `"no transition init+session.failed"`, the row's state MUST
  remain `INIT`, and the stub action MUST NOT have been awaited
