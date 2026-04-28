## ADDED Requirements

### Requirement: Actions MUST set escalated_reason before emitting ESCALATED-bound events

Any orchestrator action that emits an event whose transition leads to `ESCALATED`
(i.e., `VERIFY_ESCALATE`, `PR_CI_TIMEOUT`, `ACCEPT_ENV_UP_FAIL`) MUST call
`req_state.update_context` to persist `escalated_reason` to the database
**before** returning the emit dict. This SHALL ensure that the `escalated_reason`
field is never null in the `req_state` context when the row's state is `escalated`.

#### Scenario: NRA-S1 start_analyze clone-failed writes reason before emit

- **GIVEN** `start_analyze` is called and the runner clone fails (non-zero `clone_rc`)
- **WHEN** the action resolves the clone failure
- **THEN** it MUST call `req_state.update_context` with `escalated_reason="clone-failed"` and MUST return `emit=VERIFY_ESCALATE`

#### Scenario: NRA-S3 start_analyze_with_finalized_intent missing-intent writes reason

- **GIVEN** `start_analyze_with_finalized_intent` is called with `ctx` missing `intake_finalized_intent`
- **WHEN** the guard check detects the missing finalized intent
- **THEN** it MUST call `req_state.update_context` with `escalated_reason="missing-finalized-intent"` and MUST return `emit=VERIFY_ESCALATE`

#### Scenario: NRA-S4 start_analyze_with_finalized_intent clone-failed writes reason

- **GIVEN** `start_analyze_with_finalized_intent` is called and the runner clone fails
- **WHEN** the clone helper returns a non-None exit code
- **THEN** it MUST call `req_state.update_context` with `escalated_reason="clone-failed"` and MUST return `emit=VERIFY_ESCALATE`

#### Scenario: NRA-S5 create_pr_ci_watch PR_CI_TIMEOUT writes reason before emit

- **GIVEN** `_run_checker` is called and the checker returns `exit_code=124` (timeout)
- **WHEN** the timeout branch is selected
- **THEN** the action MUST call `req_state.update_context` with `escalated_reason="pr-ci-timeout"` and MUST return `emit=PR_CI_TIMEOUT`

#### Scenario: NRA-S6 create_accept ACCEPT_ENV_UP_FAIL writes reason before emit

- **GIVEN** `create_accept` is called and `resolve_integration_dir` returns `dir=None`
- **WHEN** the no-integration-dir branch is taken
- **THEN** the action MUST call `req_state.update_context` with `escalated_reason="accept-env-up-failed"` and MUST return `emit=ACCEPT_ENV_UP_FAIL`

### Requirement: escalate action MUST write escalated_reason to DB before side effects

The `escalate` action MUST persist the resolved `escalated_reason` to the database
via `req_state.update_context` **before** executing any side-effectful operations
(GH incident creation, BKD tag updates). This SHALL guarantee that even if a
side-effect step raises an exception mid-handler, the `escalated_reason` field in
the database is not null. Additionally, if `final_reason` is empty or falsy after
the resolution priority chain, it SHALL default to the string `"unknown"` and
emit a warning log entry.

#### Scenario: NRA-S7 escalate defaults reason to unknown when none is pre-set

- **GIVEN** `escalate` is called with a non-SESSION_END event and no `escalated_reason` in ctx
- **WHEN** the action's final_reason resolution chain produces an empty string
- **THEN** it MUST default `final_reason` to `"unknown"`, MUST emit a warning log `escalate.reason_missing_defaulted`, and MUST call `req_state.update_context` with `escalated_reason="unknown"` before the GH incident loop

#### Scenario: NRA-S8 escalate preserves pre-set reason from ctx

- **GIVEN** `escalate` is called and `ctx` contains `escalated_reason="pr-ci-timeout"`
- **WHEN** the action resolves `final_reason`
- **THEN** it MUST use `"pr-ci-timeout"` as the final reason and MUST NOT overwrite it with a different value
