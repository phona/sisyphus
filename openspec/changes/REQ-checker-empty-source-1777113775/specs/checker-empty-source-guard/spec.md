## ADDED Requirements

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

The shell template emitted by `dev_cross_check._build_cmd` SHALL maintain
a `ran` counter incremented only when the repo has a checkout-able
`feat/<REQ>` branch AND the repo's root `Makefile` contains the
`^ci-lint:` target (the existing `grep -q '^ci-lint:'` test). If `ran -eq 0`
after the loop, the script MUST exit with code 1 and stderr MUST contain the
literal substring `FAIL dev_cross_check` and `0 source repos eligible`.

#### Scenario: CESG-S6 dev_cross_check exits non-zero when no repo has feat/<REQ> + ci-lint target

- **GIVEN** `/workspace/source/repo-a` exists with `feat/REQ-X` checked out but
  no `ci-lint:` target in its Makefile (mis-configured source repo)
- **WHEN** the dev_cross_check shell template runs against `REQ-X`
- **THEN** the repo is skipped via the existing `[skip] repo-a: no make ci-lint target`
  path, `ran` stays 0, and the post-loop guard fires with exit code 1 and
  stderr containing `0 source repos eligible`

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

The shell template emitted by `staging_test._build_cmd` SHALL maintain a `ran`
counter incremented only when the repo has a checkout-able `feat/<REQ>`
branch AND the Makefile contains both `^ci-unit-test:` and
`^ci-integration-test:` targets. If `ran -eq 0` after the dispatch loop (and
before the wait loop), the script MUST exit with code 1 and stderr MUST
contain the literal substring `FAIL staging_test` and `0 source repos eligible`.
The `ran -eq 0` check MUST run before `for pid_name in $pids; do wait $pid; done`
so the script does not block waiting on a non-existent pid list.

#### Scenario: CESG-S9 staging_test exits non-zero when no repo has both ci-unit-test and ci-integration-test

- **GIVEN** `/workspace/source/repo-a` exists with `feat/REQ-X` checked out and
  Makefile that has `ci-unit-test:` but lacks `ci-integration-test:`
- **WHEN** the staging_test shell template runs against `REQ-X`
- **THEN** the repo hits the `[skip] repo-a: missing ci-unit-test or
  ci-integration-test target` path, `ran` stays 0, and the post-loop guard
  fires with exit code 1 and stderr containing `0 source repos eligible`
