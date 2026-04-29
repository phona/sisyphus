## Stage: contract / spec
- [x] Author openspec/changes/REQ-532/proposal.md with scenario definitions
- [x] Author openspec/changes/REQ-532/specs/multi-repo-e2e/spec.md

## Stage: implementation (test-only increment)
- [x] Create test_contract_multi_repo_e2e.py with 38 scenarios:
  - MREPO-CLONE-S1..S9: _clone.py 5-layer fallback + multi-repo clone
  - MREPO-CHKR-S1..S5: dev_cross_check / staging_test per-repo traversal
  - MREPO-LINK-S1..S5: pr_links discovery, caching, cross-repo isolation
  - MREPO-RUN-S1..S4: stage_run per-REQ per-stage归属
  - MREPO-ART-S1..S3: artifact_checks isolation + flake-retry fields
  - MREPO-ACC-S1..S4: accept phase multi-repo env-up/smoke/down
  - MREPO-PR-S1..S2: pr_ci_watch repos list + runner discovery
  - MREPO-ESCALATE-S1: per-repo GH incident
  - MREPO-STATE-S1..S3: ctx fields for multi-repo
  - MREPO-SUPERSEDE-S1: per-repo openspec supersede
  - MREPO-INTAKE-S1: start_execute_with_finalized_intent multi-repo clone
- [x] ruff lint + format pass
- [x] All 38 tests pass
- [x] Related existing contract tests still pass (no regression)

## Stage: PR
- [ ] git push feat/REQ-532
- [ ] gh pr create with sisyphus label
