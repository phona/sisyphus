# Tasks — REQ-ttpos-biz-pr-landability-1777247423

## Stage: investigate

- [x] Pull `ttpos-server-go#217` metadata, files, statusCheckRollup, annotations
- [x] Pull `ttpos-arch-lab#10` metadata, files, statusCheckRollup, branch contents
- [x] Resolve why each failing check on `#217` fails (annotation-level, not log-level)
- [x] Confirm `ttpos-arch-lab` repo has no `.github/workflows/` (root cause for `#10` having zero checks)
- [x] Cross-check predecessor PR fates: `ttpos-server-go#214` and `ttpos-arch-lab#9` (both CLOSED unmerged)
- [x] Confirm sisyphus resolver flip (`REQ-flip-integration-resolver-source-1777195860`) source-first semantics from sibling spec + `_integration_resolver` tests
- [x] Read `Makefile` on both feat branches to verify sisyphus contract targets present
- [x] Walk PR #10's `git log origin/main..HEAD` to surface the broken-stacking-on-#9 problem (41 commits ahead, only 3 from this REQ)

## Stage: spec / audit deliverable

- [x] Author `proposal.md` (this REQ's why + scope, read-only stance)
- [x] Author `specs/pr-landability-audit/spec.md` with 6 ADDED Requirements (LAND-S1..LAND-S6) — reusable PR-pair landability checklist
- [x] Author `audit-report.md` applying LAND-S1..LAND-S6 to each PR with PASS / BLOCKED / RISK
- [x] Cross-PR coupling §3 — landing-order resolver-deadlock (SDA-S7) discussion
- [x] Recommendations §4 — concrete next actions per PR + a sisyphus follow-up

## Stage: PR

- [x] Push `feat/REQ-ttpos-biz-pr-landability-1777247423` to `phona/sisyphus`
- [x] `gh label create sisyphus --force` + `gh pr create --label sisyphus`
- [x] PR body links to the two audited PRs + the audit-report.md path
- [x] Confirm openspec validate --strict on the change (let sisyphus spec_lint re-confirm independently)

## Stage: BKD

- [x] Update BKD issue tags: keep `analyze` + `REQ-ttpos-biz-pr-landability-1777247423`
- [x] Move BKD issue to `review` status
