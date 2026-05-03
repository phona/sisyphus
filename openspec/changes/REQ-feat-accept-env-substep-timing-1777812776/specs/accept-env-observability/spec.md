# accept-env-observability Spec Delta

## ADDED Requirements

### Requirement: accept-env-up MAY emit sub_steps array for per-phase timing observability

The `accept-env-up` Makefile target's stdout final-line JSON SHALL accept an
optional `sub_steps` field. The system MUST treat the field as optional: when it
is absent, malformed, not a list, or contains zero entries, the orchestrator MUST
proceed exactly as if the field were never declared (no failure, no warning to
the user, only a structured `info` / `warning` log so issues are diagnosable).

When `sub_steps` is present and well-formed, each entry MUST be an object with at
least `name: str` (kebab/snake-case identifier of the phase, e.g. `lab-helm`,
`thanatos-helm`, `redroid-boot`, `apk-install`) and `duration_sec: number` (the
elapsed wall-clock seconds for that phase). Additional keys (e.g. `started_at`,
`exit_code`) are allowed and MUST be ignored by the orchestrator.

The orchestrator MUST persist each well-formed sub-step entry as one
`stage_runs` row keyed by `stage = "accept-env-up." || name`, with
`outcome = "pass"`, `duration_sec` copied from the JSON, `started_at` derived
as `now() - duration_sec`, and `ended_at = now()`. Persistence MUST occur
only after the parent `accept-env-up` exits 0; an env-up failure MUST NOT
emit sub-step rows even if a partial `sub_steps` payload was printed.

#### Scenario: SUBSTEP-S1 well-formed sub_steps array → one stage_runs row per entry

- **GIVEN** `accept-env-up` exits 0 with stdout final line containing
  `sub_steps: [{"name": "lab-helm", "duration_sec": 45.2}, {"name": "apk", "duration_sec": 31.7}]`
- **WHEN** `create_accept` parses the env-up stdout
- **THEN** two `stage_runs` inserts occur with stages
  `"accept-env-up.lab-helm"` and `"accept-env-up.apk"`
- **AND** each row's `outcome` is `"pass"`
- **AND** each row's `duration_sec` matches the JSON value
- **AND** the parent `accept.pass` event still emits unchanged

#### Scenario: SUBSTEP-S2 missing sub_steps field → zero inserts, no warning

- **GIVEN** `accept-env-up` exits 0 with stdout `{"endpoint": "http://x", "namespace": "y"}` (no sub_steps key)
- **WHEN** `create_accept` parses the env-up stdout
- **THEN** zero `stage_runs` inserts occur for sub-steps
- **AND** the parent flow is unaffected

#### Scenario: SUBSTEP-S3 malformed sub_steps payload → zero inserts, warning logged

- **GIVEN** `accept-env-up` exits 0 with stdout containing `sub_steps: "not-a-list"` or `sub_steps: [{"name": 5}]` (entry missing duration_sec or wrong type)
- **WHEN** `create_accept` parses the env-up stdout
- **THEN** zero `stage_runs` inserts occur for sub-steps
- **AND** a structured warning log is emitted with key `create_accept.sub_steps_malformed`
- **AND** no exception escapes `create_accept`

#### Scenario: SUBSTEP-S4 env-up fails with partial sub_steps → no rows persisted

- **GIVEN** `accept-env-up` exits non-zero (e.g. apk-install failed) but stdout contained `sub_steps: [{"name": "lab-helm", "duration_sec": 45.2}]`
- **WHEN** `create_accept` handles the env-up failure
- **THEN** zero `stage_runs` inserts occur for sub-steps
- **AND** the action emits `accept.env-up.fail` as before

### Requirement: accept-env-down SHALL respect KEEP_ENV=1 to skip teardown

The `accept-env-down` Makefile target on integration repos SHALL recognize the
`KEEP_ENV` environment variable. When `KEEP_ENV=1`, the target MUST exit 0
immediately without performing any `helm uninstall` or `kubectl delete namespace`
operation, leaving the lab environment intact for reuse on the next
`accept-env-up` invocation. Any other value (including unset, empty string, or
`KEEP_ENV=0`) MUST trigger the normal teardown behavior.

The orchestrator MUST inject `KEEP_ENV=1` into the `teardown_accept_env` exec
environment if and only if `settings.accept_keep_env` is true. The default value
of `settings.accept_keep_env` MUST be `false` so that out-of-the-box behavior
remains "always tear down" (preserves backward compatibility for existing
integration repos that do not yet implement the KEEP_ENV branch).

When `KEEP_ENV=1` is in effect, the teardown action MUST still:
- emit the appropriate `teardown.done.pass` / `teardown.done.fail` event based on
  prior `accept_result` (the gate is unchanged)
- log `teardown.keep_env_active` at info level so operators can correlate skipped
  teardowns with retained namespaces

#### Scenario: KEEP-S1 settings.accept_keep_env=true → KEEP_ENV=1 in exec env

- **GIVEN** `settings.accept_keep_env = True`
- **AND** the runner controller is available with an integration dir
- **WHEN** `teardown_accept_env` runs
- **THEN** the env passed to `exec_in_runner` contains `KEEP_ENV=1`
- **AND** an info log `teardown.keep_env_active` is emitted

#### Scenario: KEEP-S2 settings.accept_keep_env=false (default) → KEEP_ENV not present

- **GIVEN** `settings.accept_keep_env = False`
- **AND** the runner controller is available with an integration dir
- **WHEN** `teardown_accept_env` runs
- **THEN** the env passed to `exec_in_runner` does NOT contain a `KEEP_ENV` key
- **AND** the existing teardown behavior is unchanged

#### Scenario: KEEP-S3 KEEP_ENV does not change emit routing

- **GIVEN** `settings.accept_keep_env = True` and `ctx.accept_result = "fail"`
- **WHEN** `teardown_accept_env` runs (env-down command short-circuits via KEEP_ENV)
- **THEN** the action still emits `teardown.done.fail`
- **AND** when `ctx.accept_result = "pass"` the action emits `teardown.done.pass`
