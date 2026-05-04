## ADDED Requirements

### Requirement: intent:test tag triggers direct STAGING_TEST_RUNNING entry

The system SHALL support `intent:test` as a BKD intent tag that, when present on a
REQ in `INIT` state alongside a `pr:owner/repo#N` tag, MUST fire the `INTENT_TEST`
event and transition the REQ directly to `STAGING_TEST_RUNNING` via `create_staging_test`
action, skipping `analyze`, `spec-lint`, `challenger`, and `dev-cross-check` stages.
The action MUST validate that a `pr:owner/repo#N` tag is present; if absent it MUST
emit `VERIFY_ESCALATE` with a descriptive reason. The action SHALL clone the target
repo and check out the PR's head branch before invoking the staging-test checker.

#### Scenario: IEP-S1 intent:test fires INTENT_TEST transition from INIT

- **GIVEN** a REQ in state `init`
- **WHEN** the BKD intent issue receives tag `intent:test` (no `staging-test` tag present)
- **THEN** the router fires `INTENT_TEST` event and the state machine transitions to
  `staging-test-running` with action `create_staging_test`

#### Scenario: IEP-S2 intent:test without pr: tag escalates

- **GIVEN** a REQ in state `init` with tag `intent:test` but no `pr:owner/repo#N` tag
- **WHEN** `create_staging_test` action runs
- **THEN** the action emits `VERIFY_ESCALATE` with reason indicating missing pr: tag

### Requirement: intent:pr_ci tag triggers direct PR_CI_RUNNING entry

The system SHALL support `intent:pr_ci` as a BKD intent tag that, when present on a
REQ in `INIT` state alongside a `pr:owner/repo#N` tag, MUST fire the `INTENT_PR_CI`
event and transition the REQ directly to `PR_CI_RUNNING` via `create_pr_ci_watch`
action, skipping all prior stages. The action MUST validate that a `pr:owner/repo#N`
tag is present; if absent it MUST emit `PR_CI_TIMEOUT` (config error path).

#### Scenario: IEP-S3 intent:pr_ci fires INTENT_PR_CI transition from INIT

- **GIVEN** a REQ in state `init`
- **WHEN** the BKD intent issue receives tag `intent:pr_ci` (no `pr-ci` tag present)
- **THEN** the router fires `INTENT_PR_CI` event and the state machine transitions to
  `pr-ci-running` with action `create_pr_ci_watch`

### Requirement: intent:accept tag triggers direct ACCEPT_RUNNING entry

The system SHALL support `intent:accept` as a BKD intent tag that, when present on a
REQ in `INIT` state alongside a `pr:owner/repo#N` tag, MUST fire the `INTENT_ACCEPT`
event and transition the REQ directly to `ACCEPT_RUNNING` via `create_accept` action,
skipping all prior stages. The action MUST validate that a `pr:owner/repo#N` tag is
present; if absent it MUST emit `ACCEPT_ENV_UP_FAIL`.

#### Scenario: IEP-S4 intent:accept fires INTENT_ACCEPT transition from INIT

- **GIVEN** a REQ in state `init`
- **WHEN** the BKD intent issue receives tag `intent:accept` (no `accept` tag present)
- **THEN** the router fires `INTENT_ACCEPT` event and the state machine transitions to
  `accept-running` with action `create_accept`

### Requirement: intent:archive tag triggers direct DONE entry

The system SHALL support `intent:archive` as a BKD intent tag that, when present on a
REQ in `INIT` state, MUST fire the `INTENT_ARCHIVE` event and transition the REQ
directly to `DONE` state with no action. This MUST work without any `pr:` tag.

#### Scenario: IEP-S5 intent:archive fires INTENT_ARCHIVE transition from INIT

- **GIVEN** a REQ in state `init`
- **WHEN** the BKD intent issue receives tag `intent:archive`
- **THEN** the router fires `INTENT_ARCHIVE` event and the state machine transitions to
  `done` with no action
