## ADDED Requirements

### Requirement: Engine.step advances INIT to ANALYZING on intent.analyze

The state-machine engine (`orchestrator/src/orchestrator/engine.py::step`) SHALL
advance a REQ from `INIT` to `ANALYZING` when it receives the `INTENT_ANALYZE`
event, and MUST dispatch the registered `start_analyze` action exactly once.
The CAS update MUST persist the new state to the underlying store before the
action handler runs. This is the canonical entry point for the main-chain
happy path; if this transition is broken, no REQ ever leaves the `INIT`
bucket.

#### Scenario: MCT-S1 INIT plus intent.analyze advances to ANALYZING

- **GIVEN** a `FakePool` row at state `init` and a stub `start_analyze` action
  registered in `REGISTRY`
- **WHEN** `engine.step` is invoked with `cur_state=INIT` and
  `event=INTENT_ANALYZE`
- **THEN** the returned dict MUST contain `action="start_analyze"` and
  `next_state="analyzing"`, the FakePool's row MUST have advanced to
  `state="analyzing"`, and the stub MUST have been invoked exactly once

### Requirement: Engine.step advances ANALYZING to ANALYZE_ARTIFACT_CHECKING on analyze.done

`engine.step` SHALL advance a REQ from `ANALYZING` to
`ANALYZE_ARTIFACT_CHECKING` when it receives the `ANALYZE_DONE` event, and
MUST dispatch `create_analyze_artifact_check` exactly once. This transition
inserts the post-analyze artifact gate that prevents agents from self-reporting
`pass` while leaving `proposal.md` / `tasks.md` / `spec.md` empty
(REQ-analyze-artifact-check-1777254586).

#### Scenario: MCT-S2 ANALYZING plus analyze.done advances to ANALYZE_ARTIFACT_CHECKING

- **GIVEN** a `FakePool` row at state `analyzing` and a stub
  `create_analyze_artifact_check` action registered
- **WHEN** `engine.step` is invoked with `cur_state=ANALYZING` and
  `event=ANALYZE_DONE`
- **THEN** the returned dict MUST contain
  `action="create_analyze_artifact_check"` and
  `next_state="analyze-artifact-checking"`, the row MUST be at
  `state="analyze-artifact-checking"`, and the stub MUST have been called once

### Requirement: Engine.step advances ANALYZE_ARTIFACT_CHECKING to SPEC_LINT_RUNNING on artifact-check pass

`engine.step` SHALL advance a REQ from `ANALYZE_ARTIFACT_CHECKING` to
`SPEC_LINT_RUNNING` when the artifact check passes
(`ANALYZE_ARTIFACT_CHECK_PASS`), and MUST dispatch `create_spec_lint` exactly
once. This is the gateway from analyze artifact validation into the
machine-checker stack.

#### Scenario: MCT-S3 ANALYZE_ARTIFACT_CHECKING plus check pass advances to SPEC_LINT_RUNNING

- **GIVEN** a `FakePool` row at state `analyze-artifact-checking` and a stub
  `create_spec_lint` action registered
- **WHEN** `engine.step` is invoked with
  `cur_state=ANALYZE_ARTIFACT_CHECKING` and
  `event=ANALYZE_ARTIFACT_CHECK_PASS`
- **THEN** the returned dict MUST contain `action="create_spec_lint"` and
  `next_state="spec-lint-running"`, the row MUST be at
  `state="spec-lint-running"`, and the stub MUST have been called once

### Requirement: Engine.step advances SPEC_LINT_RUNNING to CHALLENGER_RUNNING on spec-lint pass

`engine.step` SHALL advance a REQ from `SPEC_LINT_RUNNING` to
`CHALLENGER_RUNNING` when `SPEC_LINT_PASS` arrives (M18 challenger between
spec_lint and dev_cross_check), and MUST dispatch `start_challenger` exactly
once.

#### Scenario: MCT-S4 SPEC_LINT_RUNNING plus spec-lint pass advances to CHALLENGER_RUNNING

- **GIVEN** a `FakePool` row at state `spec-lint-running` and a stub
  `start_challenger` action registered
- **WHEN** `engine.step` is invoked with `cur_state=SPEC_LINT_RUNNING` and
  `event=SPEC_LINT_PASS`
- **THEN** the returned dict MUST contain `action="start_challenger"` and
  `next_state="challenger-running"`, the row MUST be at
  `state="challenger-running"`, and the stub MUST have been called once

### Requirement: Engine.step advances CHALLENGER_RUNNING to DEV_CROSS_CHECK_RUNNING on challenger pass

`engine.step` SHALL advance a REQ from `CHALLENGER_RUNNING` to
`DEV_CROSS_CHECK_RUNNING` when `CHALLENGER_PASS` arrives, and MUST dispatch
`create_dev_cross_check` exactly once. The challenger's contract test commit
on the feat branch is the precondition the next stage's `make ci-lint` reads.

#### Scenario: MCT-S5 CHALLENGER_RUNNING plus challenger pass advances to DEV_CROSS_CHECK_RUNNING

- **GIVEN** a `FakePool` row at state `challenger-running` and a stub
  `create_dev_cross_check` action registered
- **WHEN** `engine.step` is invoked with `cur_state=CHALLENGER_RUNNING` and
  `event=CHALLENGER_PASS`
- **THEN** the returned dict MUST contain `action="create_dev_cross_check"`
  and `next_state="dev-cross-check-running"`, the row MUST be at
  `state="dev-cross-check-running"`, and the stub MUST have been called once

### Requirement: Engine.step advances DEV_CROSS_CHECK_RUNNING to STAGING_TEST_RUNNING on dev-cross-check pass

`engine.step` SHALL advance a REQ from `DEV_CROSS_CHECK_RUNNING` to
`STAGING_TEST_RUNNING` when `DEV_CROSS_CHECK_PASS` arrives, and MUST dispatch
`create_staging_test` exactly once.

#### Scenario: MCT-S6 DEV_CROSS_CHECK_RUNNING plus dev-cross-check pass advances to STAGING_TEST_RUNNING

- **GIVEN** a `FakePool` row at state `dev-cross-check-running` and a stub
  `create_staging_test` action registered
- **WHEN** `engine.step` is invoked with `cur_state=DEV_CROSS_CHECK_RUNNING`
  and `event=DEV_CROSS_CHECK_PASS`
- **THEN** the returned dict MUST contain `action="create_staging_test"` and
  `next_state="staging-test-running"`, the row MUST be at
  `state="staging-test-running"`, and the stub MUST have been called once

### Requirement: Engine.step advances STAGING_TEST_RUNNING to PR_CI_RUNNING on staging-test pass

`engine.step` SHALL advance a REQ from `STAGING_TEST_RUNNING` to
`PR_CI_RUNNING` when `STAGING_TEST_PASS` arrives, and MUST dispatch
`create_pr_ci_watch` exactly once. This is the bridge from internal staging
to GitHub-side PR CI watching.

#### Scenario: MCT-S7 STAGING_TEST_RUNNING plus staging-test pass advances to PR_CI_RUNNING

- **GIVEN** a `FakePool` row at state `staging-test-running` and a stub
  `create_pr_ci_watch` action registered
- **WHEN** `engine.step` is invoked with `cur_state=STAGING_TEST_RUNNING` and
  `event=STAGING_TEST_PASS`
- **THEN** the returned dict MUST contain `action="create_pr_ci_watch"` and
  `next_state="pr-ci-running"`, the row MUST be at `state="pr-ci-running"`,
  and the stub MUST have been called once

### Requirement: Engine.step advances PR_CI_RUNNING to ACCEPT_RUNNING on pr-ci pass

`engine.step` SHALL advance a REQ from `PR_CI_RUNNING` to `ACCEPT_RUNNING`
when `PR_CI_PASS` arrives, and MUST dispatch `create_accept` exactly once.

#### Scenario: MCT-S8 PR_CI_RUNNING plus pr-ci pass advances to ACCEPT_RUNNING

- **GIVEN** a `FakePool` row at state `pr-ci-running` and a stub
  `create_accept` action registered
- **WHEN** `engine.step` is invoked with `cur_state=PR_CI_RUNNING` and
  `event=PR_CI_PASS`
- **THEN** the returned dict MUST contain `action="create_accept"` and
  `next_state="accept-running"`, the row MUST be at `state="accept-running"`,
  and the stub MUST have been called once

### Requirement: Engine.step advances ACCEPT_RUNNING to ACCEPT_TEARING_DOWN on accept pass

`engine.step` SHALL advance a REQ from `ACCEPT_RUNNING` to
`ACCEPT_TEARING_DOWN` when `ACCEPT_PASS` arrives, and MUST dispatch
`teardown_accept_env` exactly once. The teardown must precede archive even on
pass — `make accept-env-down` is non-optional (REQ-accept-contract-docs).

#### Scenario: MCT-S9 ACCEPT_RUNNING plus accept pass advances to ACCEPT_TEARING_DOWN

- **GIVEN** a `FakePool` row at state `accept-running` and a stub
  `teardown_accept_env` action registered
- **WHEN** `engine.step` is invoked with `cur_state=ACCEPT_RUNNING` and
  `event=ACCEPT_PASS`
- **THEN** the returned dict MUST contain `action="teardown_accept_env"` and
  `next_state="accept-tearing-down"`, the row MUST be at
  `state="accept-tearing-down"`, and the stub MUST have been called once

### Requirement: Engine.step advances ACCEPT_TEARING_DOWN to ARCHIVING on teardown done pass

`engine.step` SHALL advance a REQ from `ACCEPT_TEARING_DOWN` to `ARCHIVING`
when `TEARDOWN_DONE_PASS` arrives (only after a preceding `accept.pass`),
and MUST dispatch `done_archive` exactly once.

#### Scenario: MCT-S10 ACCEPT_TEARING_DOWN plus teardown done pass advances to ARCHIVING

- **GIVEN** a `FakePool` row at state `accept-tearing-down` and a stub
  `done_archive` action registered
- **WHEN** `engine.step` is invoked with `cur_state=ACCEPT_TEARING_DOWN` and
  `event=TEARDOWN_DONE_PASS`
- **THEN** the returned dict MUST contain `action="done_archive"` and
  `next_state="archiving"`, the row MUST be at `state="archiving"`, and the
  stub MUST have been called once

### Requirement: Engine.step advances ARCHIVING to DONE on archive done

`engine.step` SHALL advance a REQ from `ARCHIVING` to the terminal `DONE`
state when `ARCHIVE_DONE` arrives. The transition has no `action`
(transition table declares `action=None`), so the engine MUST return
`action="no-op"` and `next_state="done"` without dispatching any handler.
This is the only happy-path transition that lands in a terminal state.

#### Scenario: MCT-S11 ARCHIVING plus archive done advances to DONE with no-op

- **GIVEN** a `FakePool` row at state `archiving` and an empty `REGISTRY`
- **WHEN** `engine.step` is invoked with `cur_state=ARCHIVING` and
  `event=ARCHIVE_DONE`
- **THEN** the returned dict MUST contain `action="no-op"` and
  `next_state="done"`, and the row MUST be at `state="done"`

### Requirement: Engine.step chains the entire main chain via emit recursion

`engine.step` SHALL recursively follow `result["emit"]` chained event names
through all 10 main-chain action transitions when each handler emits the
next event in the canonical happy-path sequence, ultimately landing the row
at the terminal `DONE` state. This contract test ensures the full chain
INIT → ANALYZING → ANALYZE_ARTIFACT_CHECKING → SPEC_LINT_RUNNING →
CHALLENGER_RUNNING → DEV_CROSS_CHECK_RUNNING → STAGING_TEST_RUNNING →
PR_CI_RUNNING → ACCEPT_RUNNING → ACCEPT_TEARING_DOWN → ARCHIVING → DONE
is internally consistent at runtime, not just statically in the transition
table.

#### Scenario: MCT-CHAIN INIT to DONE via 11 chained emits

- **GIVEN** a `FakePool` row at state `init` and ten stub actions registered
  (`start_analyze`, `create_analyze_artifact_check`, `create_spec_lint`,
  `start_challenger`, `create_dev_cross_check`, `create_staging_test`,
  `create_pr_ci_watch`, `create_accept`, `teardown_accept_env`,
  `done_archive`), each returning `{"emit": "<next-canonical-event>"}` per
  the main-chain happy path
- **WHEN** `engine.step` is invoked once at `cur_state=INIT` with
  `event=INTENT_ANALYZE`
- **THEN** the call MUST NOT raise, the FakePool's row MUST end at
  `state="done"`, each of the ten action stubs MUST have been called
  exactly once, and the recursion guard MUST NOT have fired
  (no `recursion >12` error in the returned chain)
