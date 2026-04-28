# Proposal: staging_test Baseline Diff

## Problem

The `staging_test` checker runs `make ci-unit-test && make ci-integration-test` against
the PR branch and treats any non-zero exit code as `STAGING_TEST_FAIL`. However, if
`main` already has lint/test failures (e.g. a prior PR merged with broken tests), every
subsequent REQ's staging_test will also fail — even though the PR itself introduced no
new failures. This causes a dead-loop: verifier escalates → PR never merges → broken
main never gets fixed.

Observed: escalated:done ratio is 181:63 (25.8% done rate). 57 escalations (31%) are
due to staging_test → verifier one-cut escalate, many on REQs whose PRs are clean.

## Solution: Two-Phase Baseline Diff

Before running tests on the PR branch, run the same tests on `origin/main` (baseline).
The true failure set is `pr_failures - baseline_failures`:
- Empty → `STAGING_TEST_PASS` (PR introduced no new failures; main itself is broken)
- Non-empty → `STAGING_TEST_FAIL` with diff context for verifier

Baseline results are cached 24h in a new `baseline_results` PG table to avoid
re-running main tests for every REQ on the same main SHA.

Baseline run failure/exception → graceful degradation to old single-phase exit-code logic.
