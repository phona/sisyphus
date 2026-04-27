## ADDED Requirements

### Requirement: Pattern-matched infra-flake classification module

The system SHALL provide a module `orchestrator/src/orchestrator/checkers/_flake.py`
that exposes a regex-based classifier `classify_failure(stdout_tail, stderr_tail,
exit_code)` returning a stable `reason_tag` string when the failure matches any
known infrastructure-flake pattern, and `None` otherwise. Callers MUST treat a
non-`None` return as authorization to retry the same command. The classifier
MUST return `None` when `exit_code == 0` regardless of textual content (a
passing exec cannot carry a flake tag). Patterns SHALL match against
`stderr_tail` first then `stdout_tail`. The initial pattern table MUST cover at
least DNS resolution failure, kubectl exec SPDY upgrade error, GitHub git/RPC
5xx, container registry rate-limit (`TOOMANYREQUESTS`), container registry
network/TLS error, Go module download network error, npm network error, and
apt-mirror Connection refused/timed out — eight categories total. The pattern
table MUST NOT match generic build failures such as
`make: *** [Makefile:..] Error 1`, generic exit codes 137/124/-1, GH auth
errors (`unauthorized: authentication required`), or `manifest unknown`/
`not found` — these are not transient and MUST be passed through unchanged so
verifier-agent retains classification authority.

#### Scenario: CIFR-S1 classify_failure returns dns tag for "Could not resolve host"

- **GIVEN** stderr_tail contains the literal substring `Could not resolve host github.com`
  and exit_code is 128
- **WHEN** `classify_failure(stdout_tail="", stderr_tail=stderr_tail, exit_code=128)` runs
- **THEN** the return value MUST equal the string `"dns"`

#### Scenario: CIFR-S2 classify_failure returns None for generic make failure

- **GIVEN** stderr_tail contains `make: *** [Makefile:42] Error 1` and stdout_tail
  contains `FAIL TestFoo` and exit_code is 2
- **WHEN** `classify_failure` runs against this input
- **THEN** the return value MUST be `None` (real business failure, not infra flake)

#### Scenario: CIFR-S3 classify_failure returns None when exit_code is zero

- **GIVEN** stderr_tail contains `Could not resolve host github.com` (which would
  otherwise match) but exit_code is 0
- **WHEN** `classify_failure` runs against this input
- **THEN** the return value MUST be `None` (passing exec cannot carry a flake tag)

### Requirement: Bounded retry helper for kubectl-exec checkers

The module `orchestrator/src/orchestrator/checkers/_flake.py` SHALL expose an
async helper `run_with_flake_retry(*, coro_factory, stage, req_id, max_retries,
backoff_sec)` that returns a tuple `(final_exec_result, attempts, flake_reason)`.
The helper MUST invoke `coro_factory()` once, and on `exit_code != 0` MUST call
`classify_failure(stdout, stderr, exit_code)`; if the classifier returns a non-
`None` tag the helper SHALL sleep `backoff_sec` and re-invoke `coro_factory()`,
up to `max_retries` additional attempts. The helper MUST re-classify after each
attempt. After `max_retries` are exhausted, the returned `flake_reason` MUST be
`"flake-retry-recovered:<tag>"` when the final attempt's exit_code is 0, and
`"flake-retry-exhausted:<tag>"` otherwise — `<tag>` MUST be the reason_tag from
the **first** classified failure, even if subsequent attempts surfaced different
errors. The helper MUST return `(result, 1, None)` immediately when the first
attempt passes or when its failure is unclassified — i.e. real business
failures MUST NOT be retried. Setting `max_retries=0` MUST disable the helper
entirely (no retry, returns `(result, 1, None)`).

#### Scenario: CIFR-S4 single passing attempt returns attempts=1 reason=None

- **GIVEN** `coro_factory` returns an `ExecResult(exit_code=0, stdout="ok", stderr="")`
  on first call, with `max_retries=2` and `backoff_sec=0`
- **WHEN** `run_with_flake_retry` runs
- **THEN** the return MUST be `(<result>, 1, None)` and `coro_factory` MUST be
  called exactly once

#### Scenario: CIFR-S5 single non-flake failure returns attempts=1 reason=None

- **GIVEN** `coro_factory` returns `ExecResult(exit_code=2, stderr="make: *** Error 1")`
  on first call, with `max_retries=2`
- **WHEN** `run_with_flake_retry` runs
- **THEN** the return MUST be `(<result>, 1, None)`, `coro_factory` MUST be called
  exactly once (no retry), and `asyncio.sleep` MUST NOT be invoked

#### Scenario: CIFR-S6 flake failure recovers on retry returns recovered reason

- **GIVEN** `coro_factory` returns `ExecResult(exit_code=128, stderr="Could not resolve host github.com")`
  on first call and `ExecResult(exit_code=0, stdout="ok")` on second call, with
  `max_retries=1` and `backoff_sec=0`
- **WHEN** `run_with_flake_retry` runs
- **THEN** the return MUST be `(<second_result>, 2, "flake-retry-recovered:dns")`,
  `coro_factory` MUST be called exactly twice, and the final exec_result.exit_code
  MUST be 0

#### Scenario: CIFR-S7 flake failure on both attempts returns exhausted reason

- **GIVEN** `coro_factory` returns `ExecResult(exit_code=128, stderr="Could not resolve host github.com")`
  on both first and second calls, with `max_retries=1` and `backoff_sec=0`
- **WHEN** `run_with_flake_retry` runs
- **THEN** the return MUST be `(<second_result>, 2, "flake-retry-exhausted:dns")`
  and `coro_factory` MUST be called exactly twice

#### Scenario: CIFR-S8 max_retries=0 disables retry on flake failure

- **GIVEN** `coro_factory` returns a flake-tagged failure on first call, with
  `max_retries=0`
- **WHEN** `run_with_flake_retry` runs
- **THEN** the return MUST be `(<first_result>, 1, None)` and `coro_factory` MUST
  be called exactly once

### Requirement: CheckResult carries attempts and reason fields

The system SHALL extend the dataclass `CheckResult` in
`orchestrator/src/orchestrator/checkers/_types.py` with an `attempts: int = 1`
field representing the total number of exec attempts (including the first
attempt), while the existing `reason: str | None = None` field MUST be used by
the three kubectl-exec checkers to record either `"flake-retry-recovered:<tag>"`
or `"flake-retry-exhausted:<tag>"` when retry occurred. The `attempts` default
MUST remain `1` so existing CheckResult construction sites that do not specify
it preserve their prior behavior. When no retry occurred (single-shot pass or
non-flake fail), `attempts` MUST be 1 and `reason` MUST be `None` (or whatever
pre-existing semantic the checker already used for `reason`, e.g. `"timeout"`).

#### Scenario: CIFR-S9 CheckResult default attempts is 1

- **GIVEN** a `CheckResult(passed=True, exit_code=0, stdout_tail="", stderr_tail="", duration_sec=1.0, cmd="x")`
  constructed without specifying `attempts` or `reason`
- **WHEN** the instance is inspected
- **THEN** `result.attempts` MUST equal 1 and `result.reason` MUST be `None`

### Requirement: spec_lint, dev_cross_check, staging_test wire bounded retry

The system SHALL replace the direct `await rc.exec_in_runner(...)` call in each
of the three kubectl-exec checkers `spec_lint.run_spec_lint`,
`dev_cross_check.run_dev_cross_check`, and `staging_test.run_staging_test` with
an invocation of `run_with_flake_retry`, passing the values from
`settings.checker_infra_flake_retry_max` and
`settings.checker_infra_flake_retry_backoff_sec`. The returned
`(exec_result, attempts, flake_reason)` triple MUST be reflected in the final
`CheckResult` (`attempts` and `reason`) so downstream `artifact_checks.insert_check`
persists them. When `settings.checker_infra_flake_retry_enabled` is `False`,
the checker MUST behave as if `max_retries=0` (single-shot, no retry). The
checker `pr_ci_watch.watch_pr_ci` MUST NOT be modified by this requirement —
it owns its own HTTP retry-until-deadline loop and retains it.

#### Scenario: CIFR-S10 dev_cross_check recovers from one DNS flake

- **GIVEN** a fake `RunnerController.exec_in_runner` that returns
  `ExecResult(exit_code=128, stderr="Could not resolve host github.com")` on the
  first call and `ExecResult(exit_code=0, stdout="lint ok")` on the second,
  with settings `checker_infra_flake_retry_enabled=True`,
  `checker_infra_flake_retry_max=1`, `checker_infra_flake_retry_backoff_sec=0`
- **WHEN** `run_dev_cross_check("REQ-X")` is awaited
- **THEN** the returned `CheckResult` MUST have `passed=True`, `exit_code=0`,
  `attempts=2`, and `reason` containing the substring `flake-retry-recovered`,
  and the fake `exec_in_runner` MUST have been called exactly twice

#### Scenario: CIFR-S11 staging_test does not retry real test failure

- **GIVEN** a fake `RunnerController.exec_in_runner` that returns
  `ExecResult(exit_code=1, stdout="FAIL TestFoo", stderr="make: *** Error 1")`
  on first call, with settings `checker_infra_flake_retry_max=2`
- **WHEN** `run_staging_test("REQ-X")` is awaited
- **THEN** the returned `CheckResult` MUST have `passed=False`, `attempts=1`,
  `reason` MUST be `None`, and the fake `exec_in_runner` MUST have been called
  exactly once

#### Scenario: CIFR-S12 pr_ci_watch is unchanged by this REQ

- **GIVEN** the source file `orchestrator/src/orchestrator/checkers/pr_ci_watch.py`
  on the feat branch for `REQ-checker-infra-flake-retry-1777247423`
- **WHEN** the file is inspected
- **THEN** it MUST NOT import from `_flake` and MUST NOT call
  `run_with_flake_retry` — pr_ci_watch retains its own HTTP retry semantics

### Requirement: artifact_checks records attempts and flake_reason columns

The Postgres table `artifact_checks` SHALL gain two new columns via migration
`0009_artifact_checks_flake.sql`: `attempts INT NOT NULL DEFAULT 1` and
`flake_reason TEXT NULL`. The function `store/artifact_checks.py::insert_check`
MUST write `result.attempts` and `result.reason` into these columns on every
insert. A partial index `idx_artifact_checks_flake_reason` ON `(flake_reason)`
WHERE `flake_reason IS NOT NULL` MUST be created so future Metabase boards can
aggregate flake categories without scanning every row. The migration MUST be
idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).

#### Scenario: CIFR-S13 migration adds attempts and flake_reason columns idempotently

- **GIVEN** a Postgres database where `artifact_checks` already exists from
  migration `0003_artifact_checks.sql` and the upgrade `0009_artifact_checks_flake.sql`
  is applied
- **WHEN** the migration runs (twice in a row to verify idempotency)
- **THEN** the table MUST have columns `attempts INT NOT NULL DEFAULT 1` and
  `flake_reason TEXT` (nullable), and the partial index
  `idx_artifact_checks_flake_reason` MUST exist; running the migration twice
  MUST NOT raise an error
