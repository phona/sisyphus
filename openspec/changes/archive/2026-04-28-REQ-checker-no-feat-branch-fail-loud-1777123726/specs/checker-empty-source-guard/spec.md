## MODIFIED Requirements

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
