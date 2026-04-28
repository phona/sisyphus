# checker-empty-source-guard Specification

## Purpose
TBD - created by archiving change REQ-checker-empty-source-1777113775. Update Purpose after archive.
## Requirements
### Requirement: spec_lint MUST exit non-zero when /workspace/source is missing or empty

The `spec_lint._build_cmd` shell template SHALL run two pre-loop guards before iterating `/workspace/source/*/` and MUST exit with code 1 if either fails. Guard A MUST fire when `/workspace/source` does not exist, emitting stderr `=== FAIL spec_lint: /workspace/source missing — refusing to silent-pass ===`. Guard B MUST fire when `/workspace/source` exists but contains zero immediate subdirectories (counted via `find /workspace/source -mindepth 1 -maxdepth 1 -type d | wc -l`), emitting stderr `=== FAIL spec_lint: /workspace/source empty (0 cloned repos) — refusing to silent-pass ===`. Both gates protect against the runner-pod scenario where the PVC was wiped or the clone helper never ran, in which case the previous template's `for repo in /workspace/source/*/; do ... done` loop iterated zero times and the trailing `[ $fail -eq 0 ]` evaluated true, returning exit code 0 — a silent-pass.

#### Scenario: CESG-S1 spec_lint exits non-zero when /workspace/source is missing

- **GIVEN** a runner pod where `/workspace/source` does not exist (PVC unmounted
  or sisyphus-clone-repos.sh never invoked)
- **WHEN** `spec_lint._build_cmd("REQ-X")` is dispatched via `kubectl exec ... -- bash -c`
- **THEN** the script MUST exit with code 1 and stderr MUST contain the literal
  substring `FAIL spec_lint` and `missing`

#### Scenario: CESG-S2 spec_lint exits non-zero when /workspace/source has zero subdirectories

- **GIVEN** a runner pod where `/workspace/source` exists but contains zero
  subdirectories (e.g. only stale lock files, or empty PVC)
- **WHEN** the spec_lint shell template runs
- **THEN** the script MUST exit with code 1 before reaching the `for repo in
  /workspace/source/*/` loop, and stderr MUST contain `FAIL spec_lint` and
  `empty`

### Requirement: spec_lint MUST exit non-zero when zero source repos are eligible

The shell template emitted by `spec_lint._build_cmd` SHALL maintain a
`ran` counter initialized to 0 and incremented exclusively inside the branch
that actually invokes `openspec validate` and `check-scenario-refs.sh`
(i.e. the repo passed the `git fetch origin feat/<REQ>` checkout AND has a
`openspec/changes/<REQ>/` directory). After the loop, if `ran -eq 0`, the
script MUST exit with code 1 and stderr MUST contain the literal substring
`FAIL spec_lint` and `0 source repos eligible`. This refuses the silent-pass
where every cloned repo is skipped because the analyze-agent did not push a
feat branch with the required openspec artifact.

#### Scenario: CESG-S3 spec_lint exits non-zero when no repo has feat/<REQ> + openspec/changes/<REQ>/

- **GIVEN** `/workspace/source/repo-a` exists (real git repo) but has no
  `feat/REQ-X` branch on origin (analyze-agent failed to push)
- **WHEN** the spec_lint shell template runs against `REQ-X`
- **THEN** the per-repo skip path triggers (`[skip] repo-a: no feat branch`),
  and after the loop the `ran=0` guard fires with exit code 1 and stderr
  message containing `0 source repos eligible`

### Requirement: dev_cross_check MUST exit non-zero when /workspace/source is missing or empty

The `dev_cross_check._build_cmd` shell template SHALL carry the same two pre-loop guards as spec_lint, with stderr prefix `=== FAIL dev_cross_check: ... ===` so the verifier can attribute the failure correctly. The template MUST exit with code 1 when `/workspace/source` does not exist OR contains zero immediate subdirectories. The implementation MUST be structurally parallel to spec_lint's guard so future checker authors copy a consistent template.

#### Scenario: CESG-S4 dev_cross_check exits non-zero when /workspace/source is missing

- **GIVEN** a runner pod where `/workspace/source` does not exist
- **WHEN** the dev_cross_check shell template runs
- **THEN** exit code MUST be 1 and stderr MUST contain `FAIL dev_cross_check`
  and `missing`

#### Scenario: CESG-S5 dev_cross_check exits non-zero when /workspace/source is empty

- **GIVEN** `/workspace/source` exists with zero subdirectories
- **WHEN** the dev_cross_check shell template runs
- **THEN** exit code MUST be 1 and stderr MUST contain `FAIL dev_cross_check`
  and `empty`

### Requirement: dev_cross_check MUST exit non-zero when zero source repos are eligible

The shell template emitted by `dev_cross_check._build_cmd` SHALL fail loud
on a per-repo basis whenever a cloned source repo lacks a `feat/<REQ>` branch
on origin. When the per-repo `git fetch origin feat/<REQ>` followed by
`git checkout -B feat/<REQ> origin/feat/<REQ>` returns non-zero, the template
MUST set `fail=1` and emit the literal stderr line
`=== FAIL dev_cross_check: <repo> has no feat/<REQ> branch on origin — refusing to silent-pass ===`
identifying the offending repo by its basename, then `continue` to evaluate
remaining repos. The template MUST NOT emit the prior silent-skip
`[skip] <repo>: no feat branch / not involved` line. The template SHALL
continue to maintain a `ran` counter incremented only when the repo has a
checkout-able `feat/<REQ>` branch AND the repo's root `Makefile` contains
the `^ci-lint:` target. After the loop, Guard C — `ran -eq 0 AND fail -eq 0`
— MUST exit with code 1 and stderr MUST contain the literal substring
`FAIL dev_cross_check`, `0 source repos eligible`, and `no make ci-lint target on feat/<REQ>`.
The `&& fail -eq 0` clause prevents the per-repo fail-loud line from being
shadowed by Guard C's misleading "0 source repos eligible" message.

#### Scenario: CNFB-S1 dev_cross_check fails loud when single cloned repo has no feat branch

- **GIVEN** `/workspace/source/repo-a` exists as a real git repo with no
  remote (so `git fetch origin feat/REQ-X` exits non-zero) — analyze-agent
  declared the repo involved but failed to push
- **WHEN** the dev_cross_check shell template runs against `REQ-X`
- **THEN** the template MUST set `fail=1`, emit stderr containing the literal
  substring `FAIL dev_cross_check: repo-a has no feat/REQ-X branch on origin`
  and `refusing to silent-pass`, NOT emit `no feat branch / not involved`,
  and exit with code 1

#### Scenario: CNFB-S3 dev_cross_check Guard C still fires when feat branch present but no ci-lint target

- **GIVEN** `/workspace/source/repo-a` exists with `feat/REQ-X` branch
  successfully checked out from origin AND a `Makefile` that lacks any
  `^ci-lint:` target (mis-configured source repo, NOT analyze-agent's failure)
- **WHEN** the dev_cross_check shell template runs against `REQ-X`
- **THEN** the repo hits the existing `[skip] repo-a: no make ci-lint target`
  branch, `ran` stays 0, `fail` stays 0, and the post-loop Guard C fires
  with exit code 1 and stderr containing `0 source repos eligible` and
  `no make ci-lint target on feat/REQ-X`. stderr MUST NOT contain
  `has no feat/` (the per-repo fail-loud line is reserved for missing-feat
  cases only).

### Requirement: staging_test MUST exit non-zero when /workspace/source is missing or empty

The `staging_test._build_cmd` shell template SHALL carry the same two pre-loop guards as spec_lint, with stderr prefix `=== FAIL staging_test: ... ===`, and MUST exit with code 1 when `/workspace/source` does not exist OR contains zero immediate subdirectories. The pre-loop guards MUST run BEFORE the `mkdir -p /tmp/staging-test-logs` and BEFORE the `pids=""` initialization, so that no parallel background subshell is dispatched when the source tree is broken.

#### Scenario: CESG-S7 staging_test exits non-zero when /workspace/source is missing

- **GIVEN** a runner pod where `/workspace/source` does not exist
- **WHEN** the staging_test shell template runs
- **THEN** exit code MUST be 1 and stderr MUST contain `FAIL staging_test`
  and `missing`

#### Scenario: CESG-S8 staging_test exits non-zero when /workspace/source is empty

- **GIVEN** `/workspace/source` exists with zero subdirectories
- **WHEN** the staging_test shell template runs
- **THEN** exit code MUST be 1 and stderr MUST contain `FAIL staging_test`
  and `empty`

### Requirement: staging_test MUST exit non-zero when zero source repos are eligible

The shell template emitted by `staging_test._build_cmd` SHALL fail loud
on a per-repo basis whenever a cloned source repo lacks a `feat/<REQ>` branch
on origin. When the per-repo `git fetch origin feat/<REQ>` followed by
`git checkout -B feat/<REQ> origin/feat/<REQ>` returns non-zero, the template
MUST set `fail=1` and emit the literal stderr line
`=== FAIL staging_test: <repo> has no feat/<REQ> branch on origin — refusing to silent-pass ===`
identifying the offending repo by its basename, then `continue` to evaluate
remaining repos. The template MUST NOT emit the prior silent-skip
`[skip] <repo>: no feat branch / not involved` line. The template SHALL
continue to maintain a `ran` counter incremented only when the repo has a
checkout-able `feat/<REQ>` branch AND the Makefile contains both
`^ci-unit-test:` and `^ci-integration-test:` targets. After the dispatch loop
and BEFORE `for pid_name in $pids; do wait $pid; done`, Guard C —
`ran -eq 0 AND fail -eq 0` — MUST exit with code 1 and stderr MUST contain
the literal substring `FAIL staging_test`, `0 source repos eligible`, and
`no ci-unit-test+ci-integration-test target on feat/<REQ>`. The `&& fail -eq 0`
clause prevents the per-repo fail-loud line from being shadowed by Guard C's
misleading "0 source repos eligible" message.

#### Scenario: CNFB-S2 staging_test fails loud when single cloned repo has no feat branch

- **GIVEN** `/workspace/source/repo-a` exists as a real git repo with no
  remote (so `git fetch origin feat/REQ-X` exits non-zero) — analyze-agent
  declared the repo involved but failed to push
- **WHEN** the staging_test shell template runs against `REQ-X`
- **THEN** the template MUST set `fail=1`, emit stderr containing the literal
  substring `FAIL staging_test: repo-a has no feat/REQ-X branch on origin`
  and `refusing to silent-pass`, NOT emit `no feat branch / not involved`,
  and exit with code 1 (without blocking on `wait $pid`)

#### Scenario: CNFB-S4 staging_test Guard C still fires when feat branch present but missing make targets

- **GIVEN** `/workspace/source/repo-a` exists with `feat/REQ-X` branch
  successfully checked out from origin AND a `Makefile` that has only
  `^ci-unit-test:` (missing `^ci-integration-test:`)
- **WHEN** the staging_test shell template runs against `REQ-X`
- **THEN** the repo hits the existing
  `[skip] repo-a: missing ci-unit-test or ci-integration-test target` branch,
  `ran` stays 0, `fail` stays 0, and the post-dispatch Guard C fires with
  exit code 1 and stderr containing `0 source repos eligible` and
  `no ci-unit-test+ci-integration-test target on feat/REQ-X`. stderr MUST
  NOT contain `has no feat/` (the per-repo fail-loud line is reserved for
  missing-feat cases only).

#### Scenario: CNFB-S5 spec_lint behavior is unchanged when feat branch missing

- **GIVEN** `/workspace/source/repo-a` exists as a real git repo with no
  remote (mirror of the dev_cross_check / staging_test fail-loud setup)
- **WHEN** the spec_lint shell template runs against `REQ-X`
- **THEN** spec_lint MUST retain its prior CESG-S3 behavior: silent skip via
  `[skip] repo-a: no feat branch`, `ran` stays 0, Guard C fires with exit
  code 1 and stderr containing `0 source repos eligible`. spec_lint stderr
  MUST NOT contain `has no feat/` (this REQ is scoped to dev_cross_check +
  staging_test only — spec changes may legitimately consolidate in a
  spec_home repo per CLAUDE.md §B.4)

#### Scenario: CNFB-S6 shell template carries new fail-loud literals

- **GIVEN** the `_build_cmd("REQ-X")` invocation of dev_cross_check or
  staging_test
- **WHEN** the returned shell string is inspected
- **THEN** it MUST contain the literal substring `has no feat/REQ-X branch on origin`
  and `refusing to silent-pass`, MUST NOT contain
  `no feat branch / not involved`, and the post-loop Guard C condition MUST
  read `[ "$ran" -eq 0 ] && [ "$fail" -eq 0 ]` so the new per-repo fail-loud
  line is the authoritative emitter when only feat branches are missing

