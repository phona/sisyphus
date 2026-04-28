# Spec — analyze post-artifact-check

## ADDED Requirements

### Requirement: analyze post-artifact-check stage gates ANALYZE_DONE → SPEC_LINT_RUNNING

The orchestrator state machine SHALL insert a mechanical post-artifact-check stage
between `ANALYZING` and `SPEC_LINT_RUNNING`. After the analyze BKD agent emits
`ANALYZE_DONE`, the system MUST transition to a new state
`ANALYZE_ARTIFACT_CHECKING` and dispatch the `create_analyze_artifact_check`
action; only after that action emits `ANALYZE_ARTIFACT_CHECK_PASS` SHALL the
state machine advance to `SPEC_LINT_RUNNING`. On
`ANALYZE_ARTIFACT_CHECK_FAIL` the state SHALL move to `REVIEW_RUNNING` and the
`invoke_verifier_for_analyze_artifact_check_fail` action SHALL be dispatched.

#### Scenario: AAC-S1 happy path inserts the new stage between analyze and spec_lint
- **GIVEN** the state machine module `orchestrator.state`
- **WHEN** a static reader inspects the `TRANSITIONS` table
- **THEN** the entry for `(ANALYZING, ANALYZE_DONE)` MUST point to
  `next_state=ANALYZE_ARTIFACT_CHECKING` with `action="create_analyze_artifact_check"`,
  AND a separate entry `(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS)`
  MUST point to `next_state=SPEC_LINT_RUNNING` with `action="create_spec_lint"`.
  The legacy direct transition `(ANALYZING, ANALYZE_DONE) → SPEC_LINT_RUNNING`
  MUST no longer exist.

#### Scenario: AAC-S2 fail routes through verifier with the dedicated handler
- **GIVEN** the state machine module
- **WHEN** the entry for `(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_FAIL)`
  is read
- **THEN** it MUST have `next_state=REVIEW_RUNNING` with
  `action="invoke_verifier_for_analyze_artifact_check_fail"`,
  AND the action handler MUST be registered in `actions._verifier`,
  AND `_STAGES` in `_verifier.py` MUST contain `"analyze_artifact_check"` so
  `invoke_verifier(stage="analyze_artifact_check", trigger="fail")` is accepted.

#### Scenario: AAC-S3 SESSION_FAILED self-loop covers the new state
- **GIVEN** the state machine module
- **WHEN** the SESSION_FAILED self-loop dictionary in `TRANSITIONS` is enumerated
- **THEN** `ANALYZE_ARTIFACT_CHECKING` MUST be one of the states whose
  `(state, SESSION_FAILED)` entry maps to `Transition(state, "escalate", ...)`,
  matching the pattern used for the other `*_RUNNING` states.

### Requirement: artifact-check shell verifies analyze deliverables in every involved repo

`checkers.analyze_artifact_check._build_cmd(req_id)` SHALL produce a POSIX shell
script that, when executed inside the runner pod, MUST:

1. Refuse to silent-pass when `/workspace/source` is missing or contains zero
   subdirectories (exit 1 with a `=== FAIL analyze-artifact-check: ... ===` marker
   on stderr).
2. Iterate every `/workspace/source/*/`, attempt
   `git fetch origin feat/<REQ>` then `git checkout -B feat/<REQ> origin/feat/<REQ>`.
   A repo whose fetch fails MUST be skipped (treated as not involved) and MUST NOT
   contribute to the failure count, mirroring `spec_lint._build_cmd`.
3. For every eligible repo (fetch succeeded **and** `openspec/changes/<REQ>/`
   exists), require that
   `openspec/changes/<REQ>/specs/<capability>/spec.md` contains at least one
   non-empty file (recursive find with `-type f -size +0`). Missing or all-empty
   `spec.md` MUST cause exit 1.
4. Require that, **across all eligible repos taken together**, at least one
   non-empty `openspec/changes/<REQ>/proposal.md` and at least one non-empty
   `openspec/changes/<REQ>/tasks.md` MUST be present. The shell MUST tolerate
   the spec-home pattern where consumer repos may carry only `spec.md`.
5. Require that the surviving non-empty `tasks.md` contains at least one
   Markdown checkbox matching `^[[:space:]]*-[[:space:]]*\[[ xX]\]` (passed to
   `grep -E`), so an empty stub passes the size check but fails the content check.
6. Refuse to silent-pass when zero repos are eligible after the fetch loop
   (exit 1 with a marker), mirroring spec_lint's `ran=0` guard.
7. Exit 0 only when every eligible repo passes (3) and the cumulative checks
   (4) and (5) hold.

#### Scenario: AAC-S4 build_cmd guards /workspace/source missing and empty
- **GIVEN** `checkers.analyze_artifact_check._build_cmd("REQ-X")` returns string `cmd`
- **WHEN** a static reader greps `cmd`
- **THEN** `cmd` MUST contain `[ ! -d /workspace/source ]` AND
  `FAIL analyze-artifact-check: /workspace/source missing` AND
  `find /workspace/source -mindepth 1 -maxdepth 1 -type d` AND
  `"$repo_count" -eq 0` AND `FAIL analyze-artifact-check: /workspace/source empty`,
  matching the empty-source guard pattern from `spec_lint._build_cmd`.

#### Scenario: AAC-S5 build_cmd checks proposal/tasks/spec/checkbox literals
- **GIVEN** `checkers.analyze_artifact_check._build_cmd("REQ-X")` returns string `cmd`
- **WHEN** a static reader greps `cmd`
- **THEN** `cmd` MUST contain literal references to
  `openspec/changes/REQ-X/proposal.md`, `openspec/changes/REQ-X/tasks.md`,
  the substring `specs` joined with `spec.md` for the recursive spec.md probe,
  AND a checkbox regex containing `\[[ xX]\]` (passed to `grep -E`),
  AND `git fetch origin "feat/REQ-X"`.

#### Scenario: AAC-S6 build_cmd refuses 0 eligible repos
- **GIVEN** `checkers.analyze_artifact_check._build_cmd("REQ-X")` returns string `cmd`
- **WHEN** a static reader greps `cmd`
- **THEN** `cmd` MUST contain `ran=0` AND `ran=$((ran+1))` AND `"$ran" -eq 0`
  AND a marker string like `0 source repos eligible` (mirroring spec_lint),
  AND it MUST end with `[ $fail -eq 0 ]` so the final exit code reflects the
  aggregated check status.

### Requirement: artifact_checks row is written for every analyze_artifact_check run

`actions.create_analyze_artifact_check` SHALL invoke
`checkers.analyze_artifact_check.run_analyze_artifact_check`, write the resulting
`CheckResult` to the `artifact_checks` table with `stage="analyze-artifact-check"`,
and MUST emit `ANALYZE_ARTIFACT_CHECK_PASS` on success or
`ANALYZE_ARTIFACT_CHECK_FAIL` on any non-zero exit / timeout / unhandled
exception.

#### Scenario: AAC-S7 pass writes artifact_checks then emits PASS event
- **GIVEN** a fake K8s controller that returns `exit_code=0` for the runner exec
- **WHEN** `create_analyze_artifact_check.create_analyze_artifact_check(...)` is awaited
- **THEN** `artifact_checks.insert_check` MUST have been called once with
  `stage="analyze-artifact-check"` and `result.passed=True`,
  AND the action's return dict MUST contain `"emit": "analyze-artifact-check.pass"`.

#### Scenario: AAC-S8 non-zero exit emits FAIL event with non-zero exit_code
- **GIVEN** a fake K8s controller that returns `exit_code=1` with marker stderr
- **WHEN** `create_analyze_artifact_check.create_analyze_artifact_check(...)` is awaited
- **THEN** the action's return dict MUST contain
  `"emit": "analyze-artifact-check.fail"`, `"passed": False`, and `"exit_code": 1`,
  AND `artifact_checks.insert_check` MUST still have been called once.
