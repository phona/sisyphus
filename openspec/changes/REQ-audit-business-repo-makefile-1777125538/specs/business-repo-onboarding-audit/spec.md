# business-repo-onboarding-audit Specification

## ADDED Requirements

### Requirement: source repo onboarding audit MUST verify all six ttpos-ci targets exist

The onboarding audit SHALL verify that a candidate source repo's `Makefile` (or include chain) defines all six ttpos-ci standard targets before that repo joins `involved_repos`. A source repo joining sisyphus's `involved_repos` (whether via `default_involved_repos` or per-REQ explicit declaration) MUST pass this audit before its first analyze REQ. The audit MUST cover all six standard targets:
`ci-env`, `ci-setup`, `ci-lint`, `ci-unit-test`, `ci-integration-test`, and
`ci-build`. A repo missing any target MUST be flagged GAP in the audit output and
MUST NOT be added to involved_repos until the gap is closed by a separate
follow-up REQ. This is a documentation-checklist requirement enforced manually
by the analyze-agent at onboarding time, not a runtime sisyphus check.

#### Scenario: AUDIT-S1 audit passes when all six targets resolve via root Makefile or include chain

- **GIVEN** a candidate source repo whose root `Makefile` defines or includes
  rules for `ci-env`, `ci-setup`, `ci-lint`, `ci-unit-test`, `ci-integration-test`,
  `ci-build` (any of them MAY come from an `include` directive)
- **WHEN** the auditor runs `make -n ci-env ci-setup ci-lint ci-unit-test ci-integration-test ci-build` against a fresh clone
- **THEN** make resolves a recipe for every target and exits 0; the audit output
  records PASS for the "all six targets" check

#### Scenario: AUDIT-S2 audit flags GAP when any target is missing from include chain

- **GIVEN** a candidate source repo whose Makefile (and its includes) defines
  only `ci-lint`, `ci-unit-test`, and `ci-integration-test` but not `ci-env`,
  `ci-setup`, or `ci-build`
- **WHEN** the auditor runs `make -n ci-env`
- **THEN** make exits non-zero with `No rule to make target` and the audit output
  records GAP for the missing targets

#### Scenario: AUDIT-S3 audit flags GAP when repo has no Makefile at all

- **GIVEN** a candidate source repo with no `Makefile` at the repo root
- **WHEN** the auditor lists the repo root via `gh api repos/<owner>/<repo>/contents`
- **THEN** the audit output records GAP "no Makefile" and the candidate
  IS NOT eligible for involved_repos until a Makefile is added

### Requirement: onboarding audit MUST verify ci-lint honors BASE_REV environment variable

The audit SHALL verify the repo's `ci-lint` recipe reads the `BASE_REV` environment
variable for incremental scoping, MUST treat empty `BASE_REV` as full-scan
fallback, and MUST exit zero on lint pass / non-zero on lint fail. The audit MAY
satisfy this by reading the recipe and confirming the shell expansion idiom
`$${BASE_REV:+--new-from-rev=$$BASE_REV}` (or an equivalent BASE_REV-checking
construct) is present in the recipe body.

#### Scenario: AUDIT-S4 ci-lint with BASE_REV expansion present

- **GIVEN** the candidate's `ci-lint` recipe contains a shell expansion
  `golangci-lint run $${BASE_REV:+--new-from-rev=$$BASE_REV}` (the canonical
  ttpos-ci form documented in sisyphus integration-contracts.md §2.2)
- **WHEN** the auditor greps the recipe text
- **THEN** the audit records PASS for "ci-lint honors BASE_REV"

#### Scenario: AUDIT-S5 ci-lint hardcodes branch reference instead of BASE_REV

- **GIVEN** the candidate's `ci-lint` recipe hardcodes `golangci-lint run --new-from-rev=origin/main`
  with no `BASE_REV` reference
- **WHEN** the auditor greps the recipe
- **THEN** the audit records GAP "ci-lint does not honor BASE_REV; will refuse
  empty input or pin to wrong base" and the candidate IS NOT eligible until fixed

### Requirement: onboarding audit MUST verify dispatch.yml routes to phona/ttpos-ci

The onboarding audit SHALL verify, for any candidate source repo that intends to drive the phona/ttpos-ci pipeline (GitHub Actions side, the lint→unit-test→sonarqube path), that the candidate has a `.github/workflows/dispatch.yml` (or equivalent
`repository_dispatch` sender) that fires `event_type` matching the ttpos-ci
target workflow (`ci-go` or `ci-flutter`). Repos that do not need ttpos-ci
GHA (sisyphus-only path) MAY skip this check; the audit MUST record N/A and
note that pr-ci-watch will then have nothing to watch (which is a separate
admission decision).

#### Scenario: AUDIT-S6 candidate has dispatch.yml firing ci-go

- **GIVEN** the candidate source repo has `.github/workflows/dispatch.yml`
  containing `event_type: ci-go` (or ci-flutter for Flutter repos)
- **WHEN** the auditor reads the workflow file
- **THEN** the audit records PASS for "ttpos-ci dispatch wired"

#### Scenario: AUDIT-S7 candidate has no dispatch.yml

- **GIVEN** the candidate's `.github/workflows/` listing contains no `dispatch.yml`
  (or any other workflow firing repository_dispatch into phona/ttpos-ci)
- **WHEN** the auditor lists the directory
- **THEN** the audit records GAP "no dispatch to ttpos-ci"; sisyphus pr-ci-watch
  will see zero check-runs on the feat branch's PR and time out (1800s default)

### Requirement: onboarding audit MUST record candidate repo's default branch

The audit SHALL record the candidate's GitHub default branch and flag a WARN
if it is not in the set `{main, develop, dev}`. WARN does not block onboarding
but signals that sisyphus's `BASE_REV` calculation chain
(`origin/main → origin/develop → origin/dev → empty`) will fall through to
empty BASE_REV, causing `ci-lint` to run as full scan rather than incremental.
The audit output MUST cite this as a known performance trade-off, not a
correctness issue.

#### Scenario: AUDIT-S8 default branch is main

- **GIVEN** the candidate's `gh api repos/<owner>/<repo>` returns `"default_branch": "main"`
- **WHEN** the auditor reads it
- **THEN** the audit records PASS and notes BASE_REV computation will succeed
  on the first chain step

#### Scenario: AUDIT-S9 default branch is release

- **GIVEN** the candidate's default_branch is `release` (or any other
  non-main/develop/dev value)
- **WHEN** the auditor reads it
- **THEN** the audit records WARN "BASE_REV chain will fall through to empty;
  ci-lint runs full scan" and flags this in the report's per-repo summary table.
  Onboarding MAY proceed; performance impact is acceptable

### Requirement: audit output MUST include a per-target pass/gap matrix and follow-up REQ candidate list

The audit deliverable SHALL include a single-table summary of every audited
target × every audited repo with cell values in the set
`{✅ PASS, ⚠️ WARN, ⚠️ STUB, ❌ GAP, N/A}`. The deliverable MUST also list
candidate follow-up REQs to close each GAP, with each candidate carrying a
proposed REQ id stem and a one-line rationale. The audit itself MUST NOT
attempt to close any GAP — closing is the responsibility of independently
filed follow-up REQs so business-repo owners retain decision authority over
their own Makefile / workflow shape.

#### Scenario: AUDIT-S10 audit report contains the matrix

- **GIVEN** an audit report markdown produced by the analyze-agent
- **WHEN** a reviewer reads it
- **THEN** there is a heading-level section containing a matrix table whose
  rows are audited targets and whose columns are audited repos, with each cell
  marked using one of the five status labels above

#### Scenario: AUDIT-S11 audit report lists follow-up REQ candidates

- **GIVEN** the audit identified at least one GAP or DOC-MISMATCH
- **WHEN** the reviewer reads the report's "后续 REQ 候选" / "follow-up REQ
  candidates" section
- **THEN** every GAP and every DOC-MISMATCH has a corresponding numbered REQ
  candidate with proposed id stem and one-line rationale. The audit deliverable
  MUST NOT itself implement any of those follow-ups
