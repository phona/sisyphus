## ADDED Requirements

### Requirement: staging_test checker performs baseline diff before judging PR failures

The `run_staging_test` checker SHALL execute a two-phase protocol before emitting
`STAGING_TEST_PASS` or `STAGING_TEST_FAIL`. In Phase 1, the checker MUST checkout
`origin/main` on each source repo and run the same `make ci-unit-test && make
ci-integration-test` suite, collecting per-repo pass/fail results ("baseline_failures").
Baseline results MUST be cached in the `baseline_results` PostgreSQL table keyed by
`"baseline:staging_test:<main_head_sha>"` with a 24-hour TTL, so subsequent REQs
running against the same main SHA reuse the cached results without re-running main tests.
In Phase 2, the checker MUST run the same test suite on the `feat/<REQ>` branch and
collect per-repo results ("pr_failures"). The checker MUST then compute
`pr_introduced_failures = pr_failures − baseline_failures`; if this set is empty, the
checker MUST return `passed=True` with `exit_code=0` and MUST NOT emit
`STAGING_TEST_FAIL`, even if Phase 2 tests exited non-zero. If `pr_introduced_failures`
is non-empty, the checker MUST return `passed=False` and MUST include a structured
"SISYPHUS BASELINE DIFF" context block in `stderr_tail` listing `baseline_failures`,
`pr_introduced_failures`, `all_pr_failures`, and `main_sha`. If Phase 1 fails or raises
an exception, the checker MUST degrade gracefully to the old single-phase logic (using
Phase 2 exit code directly) without propagating the baseline failure.

#### Scenario: BD-1 baseline all pass and PR all pass yields pass

- **GIVEN** baseline Phase 1 runs on origin/main and all repos pass (`baseline_failures = {}`)
- **AND** Phase 2 runs on feat/<REQ> and all repos pass (`pr_failures = {}`)
- **WHEN** `run_staging_test` completes
- **THEN** the returned `CheckResult.passed` MUST be `True` and `exit_code` MUST be `0`

#### Scenario: BD-2 baseline failures are identical to PR failures yields pass

- **GIVEN** baseline Phase 1 reports `baseline_failures = {"repo-a"}` (repo-a fails on main)
- **AND** Phase 2 reports `pr_failures = {"repo-a"}` (same set, PR introduced nothing new)
- **WHEN** `run_staging_test` completes
- **THEN** `CheckResult.passed` MUST be `True` and `exit_code` MUST be `0`
- **AND** `stdout_tail` MUST contain the string `"SISYPHUS BASELINE DIFF"` to record the override
- **AND** `stdout_tail` MUST contain `"pr_introduced_failures"` showing an empty list

#### Scenario: BD-3 PR introduces new failures beyond baseline yields fail with diff context

- **GIVEN** baseline Phase 1 reports `baseline_failures = {"repo-a"}` (repo-a fails on main)
- **AND** Phase 2 reports `pr_failures = {"repo-a", "repo-b"}` (repo-b is new)
- **WHEN** `run_staging_test` completes
- **THEN** `CheckResult.passed` MUST be `False` and `exit_code` MUST be non-zero
- **AND** `stderr_tail` MUST contain `"SISYPHUS BASELINE DIFF"` with the diff context
- **AND** `stderr_tail` MUST contain `"repo-b"` in the `pr_introduced_failures` field

#### Scenario: BD-4 baseline phase exception causes graceful degradation

- **GIVEN** the SHA-get exec call raises a runtime exception (e.g. kubectl channel closed)
- **AND** Phase 2 runs on feat/<REQ> and exits with code 1
- **WHEN** `run_staging_test` completes
- **THEN** `CheckResult.passed` MUST be `False` (old single-phase exit-code logic)
- **AND** `stderr_tail` MUST NOT contain `"SISYPHUS BASELINE DIFF"` (no baseline context injected)
