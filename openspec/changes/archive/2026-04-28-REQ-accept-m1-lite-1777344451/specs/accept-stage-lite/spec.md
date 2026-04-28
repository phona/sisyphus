## ADDED Requirements

### Requirement: accept stage runs per-repo env-up + smoke + env-down without BKD agent

The system SHALL implement the accept stage as a mechanical checker (no BKD agent
dispatch) that iterates over all cloned repos in `/workspace/source/*/` and for each
repo executes three phases: `make accept-env-up`, `make accept-smoke`, and
`make accept-env-down`. The stage MUST emit `accept.pass` when all repos pass and
`accept.fail` when any repo fails. The implementation MUST store `accept_result`
(`"pass"` or `"fail"`) and `accept_fail_repos` (list of failing repo names) in
`ctx` before emitting to ensure `teardown_accept_env` can route correctly.

#### Scenario: AML-S1 all repos pass emits accept.pass with ctx accept_result=pass

- **GIVEN** `ctx.cloned_repos` contains one or more repos
- **AND** the accept script exits 0 with stdout ending in `PASS`
- **WHEN** `create_accept` runs
- **THEN** the action emits `accept.pass`
- **AND** `ctx.accept_result` is set to `"pass"`

#### Scenario: AML-S2 any repo env-up fail emits accept.fail with fail_repos in ctx

- **GIVEN** `ctx.cloned_repos` contains one or more repos
- **AND** the accept script exits 1 with stdout ending in `FAIL:repo-a`
- **WHEN** `create_accept` runs
- **THEN** the action emits `accept.fail`
- **AND** the return value contains `fail_repos: ["repo-a"]`
- **AND** `ctx.accept_result` is set to `"fail"`
- **AND** `ctx.accept_fail_repos` contains `"repo-a"`

#### Scenario: AML-S3 repo without accept-env-up target is skipped not failed

- **GIVEN** a repo exists in `/workspace/source/` without an `accept-env-up` Makefile target
- **WHEN** the accept script runs
- **THEN** that repo is skipped with a warning logged to stderr
- **AND** the skip does NOT set `fail=1`
- **AND** overall result is still `PASS` if no other repo fails

#### Scenario: AML-S4 empty cloned_repos returns accept.pass without exec

- **GIVEN** `ctx.cloned_repos` is empty or absent
- **WHEN** `create_accept` runs
- **THEN** the action emits `accept.pass` immediately (vacuous true)
- **AND** `exec_in_runner` is NOT called

### Requirement: teardown_accept_env reads accept_result from ctx before tags

The system SHALL update `teardown_accept_env` to check `ctx.accept_result` before
reading `result:pass`/`result:fail` BKD tags. When `ctx.accept_result` is set (by the
new mechanical `create_accept`), it MUST take precedence. When it is absent (legacy
BKD-agent flow), the action MUST fall back to the `result:pass` tag in `tags`. This
ensures correct routing to `TEARDOWN_DONE_PASS` or `TEARDOWN_DONE_FAIL` regardless
of whether the accept result came from the new script or the legacy agent.

#### Scenario: AML-S5 teardown reads ctx.accept_result=pass and emits teardown-done.pass

- **GIVEN** `ctx.accept_result` is `"pass"` (set by new create_accept)
- **AND** tags do NOT contain `result:pass`
- **WHEN** `teardown_accept_env` runs
- **THEN** `accept_result` is `"pass"` (from ctx)
- **AND** the action emits `teardown-done.pass`

#### Scenario: AML-S6 teardown falls back to tags when ctx.accept_result is absent

- **GIVEN** `ctx.accept_result` is absent (legacy BKD-agent path)
- **AND** tags contain `result:pass`
- **WHEN** `teardown_accept_env` runs
- **THEN** `accept_result` is `"pass"` (from tags fallback)
- **AND** the action emits `teardown-done.pass`
