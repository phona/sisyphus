# Tasks — REQ-spec-pattern-contract-amendment-1777818469

> spec-only amendment. No business code. No tests. Sisyphus checkers (`spec_lint` +
> `dev_cross_check` + `staging_test`) only enforce that the openspec delta validates and
> that the repo's lint/test baselines stay green.

## Stage: contract / spec
- [x] author proposal.md — motivation, scope, non-goals, roll-out plan
- [x] author tasks.md — this file
- [x] author specs/feat-cross-repo-env-orchestration/spec.md delta:
  - [x] MODIFIED Requirements: R1 (emits schema) — preserve CREO-S1..S5, add EPCA-S1..S3
  - [x] MODIFIED Requirements: R4 (sequential accept-env-up) — preserve CREO-S14..S17 semantics, add EPCA-S4 covering pattern-form short-circuit
  - [x] MODIFIED Requirements: R5 (endpoint values) — replace narrative; preserve CREO-S18..S20 byte-faithful; add EPCA-S5..S6
  - [x] ADDED Requirements: R12 pre-resolve phase — scenarios EPCA-S7..S10
- [ ] (skipped) author design.md — single-page trade-off doc covered inline in proposal.md "Why" / "Out-of-scope"; no separate file needed for an amendment of this size
- [x] `openspec validate REQ-spec-pattern-contract-amendment-1777818469` green
- [x] `check-scenario-refs.sh --specs-search-path /workspace/source .` green (no scenario refs in non-spec files for this REQ)

## Stage: implementation
- [x] no implementation in this REQ — pure spec amendment

## Stage: PR (gates before push)
- [x] `make ci-lint` — N/A for spec-only change; checker runs over no-op diff
- [x] `make ci-unit-test` — N/A for spec-only change
- [x] `make ci-integration-test` — N/A for spec-only change
- [x] git push feat/REQ-spec-pattern-contract-amendment-1777818469
- [x] gh pr create --label sisyphus, body uses `Refs #342 #359` (NOT `Closes` — impl REQs close)

## Stage: handoff (after PR merge, separate REQs)
- [ ] (followup REQ) sisyphus orch pattern resolver impl — see #359 Phase B
- [ ] (followup REQ) ttpos-server-go `.sisyphus/env.yaml` pattern adoption — see #359 Phase C
- [ ] (followup REQ) ttpos-flutter `.sisyphus/env.yaml` pattern adoption (supersedes flutter#167) — see #359 Phase C
