# PR Landability Audit Checklist

## ADDED Requirements

### Requirement: PR head branch tracks its REQ tag

The audit SHALL verify that every audited PR's head branch name matches
the pattern `feat/<REQ-id>` and that the same `<REQ-id>` appears either
in the PR title or in a `REQ:` line in the PR body. The audit MUST mark
LAND-S1 as PASS when both conditions hold and as RISK when either is
missing or mismatched.

#### Scenario: LAND-S1 — branch name and REQ tag align
- **GIVEN** an audited PR with head ref `feat/REQ-foo-1234`
- **AND** PR title or body containing the literal string `REQ-foo-1234`
- **WHEN** the audit applies the LAND-S1 check
- **THEN** the result for that PR's LAND-S1 cell SHALL be `PASS`

### Requirement: PR base branch is the repo's actual default

The audit SHALL fetch the target repo's default branch via
`gh repo view <owner>/<repo> --json defaultBranchRef` and compare it
to the audited PR's `baseRefName`. The audit MUST mark LAND-S2 as PASS
when they match exactly and as RISK when the PR is targeting a
non-default branch (e.g. `develop`, a release-train branch, or a stale
fork) without an explicit justification in the PR body.

#### Scenario: LAND-S2 — base equals defaultBranchRef
- **GIVEN** a repo whose `defaultBranchRef.name` is `release`
- **AND** an audited PR with `baseRefName = release`
- **WHEN** the audit applies the LAND-S2 check
- **THEN** the result for that PR's LAND-S2 cell SHALL be `PASS`

### Requirement: PR has actionable CI signal

The audit SHALL fetch `statusCheckRollup` for every audited PR and
inspect every entry whose `conclusion` is `failure`. For each failing
check the audit MUST also fetch `check-runs/<id>/annotations` and
classify the failure as one of `code-failed` (the workflow ran and a
real assertion failed), `platform-rejected` (the workflow never started
because of GHA billing, quota, permissions, or other org-level
infrastructure), or `infra-flake` (the workflow started but exited on
a non-deterministic infrastructure issue). The audit MUST mark LAND-S3
as BLOCKED when any failure is `platform-rejected` or when the rollup
is empty because the repo has no `.github/workflows/`, and as RISK
when the only failures are `infra-flake`. The audit SHALL never count
author-asserted "ran in the runner pod, passed" claims as CI signal —
those belong to LAND-S5.

#### Scenario: LAND-S3 — billing-rejected check counts as BLOCKED
- **GIVEN** an audited PR whose `statusCheckRollup` contains a check with `conclusion = failure`
- **AND** that check's annotation message starts with "The job was not started because recent account payments have failed"
- **WHEN** the audit applies the LAND-S3 check
- **THEN** the result for that PR's LAND-S3 cell SHALL be `BLOCKED — GHA billing`
- **AND** the audit recommendation MUST attribute the fix to an org-admin action, not to the PR author

#### Scenario: LAND-S3 — repo with zero workflows counts as BLOCKED
- **GIVEN** an audited PR whose `statusCheckRollup` is `[]`
- **AND** `gh api repos/<owner>/<repo>/contents/.github/workflows` returns HTTP 404 on the PR's head ref
- **WHEN** the audit applies the LAND-S3 check
- **THEN** the result for that PR's LAND-S3 cell SHALL be `BLOCKED — repo has no .github/workflows/`
- **AND** the audit recommendation MUST propose a follow-up REQ to port the sisyphus contract checks into a workflow

### Requirement: PR's Makefile honors the sisyphus contract targets

The audit SHALL grep the PR's branch `Makefile` (and any included
fragments like `ttpos-scripts/*.mk`) for `^ci-lint:`, `^ci-unit-test:`,
and `^ci-integration-test:`. For source repos that the sisyphus
accept stage will route to (i.e. those that ship `accept-env-up:`),
the audit MUST additionally verify `^accept-env-up:` and
`^accept-env-down:` are defined. The audit MUST mark LAND-S4 as PASS
only when every required target is present; missing targets fail with
the exact target name surfaced in the report.

#### Scenario: LAND-S4 — all five contract targets present
- **GIVEN** an audited PR's branch Makefile + included fragments contain all of `ci-lint`, `ci-unit-test`, `ci-integration-test`, `accept-env-up`, `accept-env-down` as `.PHONY` recipes
- **WHEN** the audit applies the LAND-S4 check
- **THEN** the result for that PR's LAND-S4 cell SHALL be `PASS`

### Requirement: PR's openspec change is structurally valid

The audit SHALL confirm that the PR ships
`openspec/changes/<REQ-id>/proposal.md` plus at least one
`openspec/changes/<REQ-id>/specs/<capability>/spec.md` written in
delta format (`## ADDED Requirements` / `## MODIFIED Requirements` /
`## REMOVED Requirements` / `## RENAMED Requirements`) and that every
`### Requirement:` heading is followed by prose containing `SHALL` or
`MUST`. The audit MAY defer the actual `openspec validate --strict`
run to sisyphus `spec_lint` and mark LAND-S5 as PASS-asserted when the
file structure looks correct on inspection. The audit MUST mark
LAND-S5 as RISK when any required file is missing or the spec.md
lacks a delta heading.

#### Scenario: LAND-S5 — delta-format spec with SHALL prose passes assertion
- **GIVEN** an audited PR's branch contains `openspec/changes/<REQ-id>/specs/<capability>/spec.md`
- **AND** that file starts with `## ADDED Requirements`
- **AND** every `### Requirement:` heading is followed (after blank lines, before the first `####`) by prose containing the literal token `SHALL` or `MUST`
- **WHEN** the audit applies the LAND-S5 check
- **THEN** the result for that PR's LAND-S5 cell SHALL be `PASS (asserted)`
- **AND** the audit report MUST flag that sisyphus `spec_lint` will independently confirm via `openspec validate --strict`

### Requirement: PR has no semantic conflict with predecessor or sibling PRs

The audit SHALL identify, for each audited PR, (a) any predecessor PR
referenced in the body and check whether it is closed-unmerged or
merged, (b) any sibling PR that ships an overlapping implementation of
the same contract surface, and (c) any in-flight sisyphus capability
change (e.g. resolver flips, contract renames) that affects whether
the PR's deliverable will actually be invoked downstream. The audit
MUST mark LAND-S6 as PASS only when no overlap or stale-stacking
condition is present. RISK is recorded when (a) a predecessor PR is
closed-unmerged but the audited PR's diff still includes commits
inherited from the predecessor's branch, (b) a sibling PR ships a
competing implementation without an explicit pick-one decision, or
(c) a sisyphus resolver / contract change between PR open time and
audit time has changed which implementation will actually be invoked.

#### Scenario: LAND-S6 — broken stacking on a closed-unmerged predecessor
- **GIVEN** an audited PR whose body declares "Pairs with PR #N (skeleton); when PR #N merges first, GitHub will rebase this PR down"
- **AND** PR #N has `state = CLOSED` and `mergedAt = null`
- **AND** `git log <repo-default>..<PR-head>` shows commits authored on PR #N's branch still present in the audited PR's diff
- **WHEN** the audit applies the LAND-S6 check
- **THEN** the result for that PR's LAND-S6 cell SHALL be `RISK`
- **AND** the audit recommendation MUST propose either rebasing on the repo default to drop the inherited commits, or closing the audited PR as superseded

#### Scenario: LAND-S6 — sibling PR competes for the same contract surface
- **GIVEN** an audited PR pair `(A, B)` whose REQ-id timestamps match (same coordinated change)
- **AND** both PRs ship a `make accept-env-up` target backed by helm charts in their respective repos
- **AND** sisyphus' `_integration_resolver._decide` (post `REQ-flip-integration-resolver-source-1777195860`) prefers source repos and returns None when multiple sources have `accept-env-up:` and no integration tie-breaker
- **WHEN** the audit applies the LAND-S6 check to both PRs
- **THEN** both PRs' LAND-S6 cells SHALL be `RISK`
- **AND** the audit recommendation MUST surface the latent SDA-S7 deadlock and propose either deprecating one implementation or filing a sisyphus follow-up to introduce an explicit tie-breaker
