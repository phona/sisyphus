## Stage: contract / spec
- [x] author openspec/changes/REQ-fix-intent-issue-hijacking-1777427339/proposal.md
- [x] author openspec/changes/REQ-fix-intent-issue-hijacking-1777427339/specs/fix-intent-issue-hijacking/spec.md

## Stage: implementation
- [x] Modify start_analyze.py: create analyze sub-issue via create_issue, use merge_tags_and_update on intent issue
- [x] Add dispatch_slugs idempotency to start_analyze.py (match intake path pattern)
- [x] Fix analyze.md.j2: handle undefined intake_summary in direct analyze path
- [x] Update test_actions_start_analyze.py for new BKD call patterns
- [x] Update test_actions_smoke.py for new behavior
- [x] Update test_actions_start_analyze_supersede.py with missing mocks
- [x] Update test_contract_multi_repo_e2e.py with missing mocks

## Stage: PR
- [ ] git push feat/REQ-fix-intent-issue-hijacking-1777427339
- [ ] gh pr create with sisyphus label