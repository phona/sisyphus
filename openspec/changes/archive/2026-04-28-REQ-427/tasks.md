# REQ-427 Tasks

## Stage: contract / spec

- [x] author specs/dispatch-idempotency/spec.md with scenarios
- [x] author specs/dispatch-idempotency/contract.spec.yaml

## Stage: implementation

- [x] migration 0010_dispatch_slugs.sql + rollback
- [x] store/dispatch_slugs.py (get / put)
- [x] invoke_verifier slug guard
- [x] start_fixer slug guard
- [x] start_analyze_with_finalized_intent slug guard
- [x] done_archive slug guard
- [x] create_accept slug guard
- [x] create_staging_test (_dispatch_bkd_agent) slug guard
- [x] start_challenger slug guard
- [x] create_pr_ci_watch (_dispatch_bkd_agent) slug guard
- [x] unit tests for dispatch_slugs store

## Stage: PR

- [x] git push feat/REQ-427
- [x] gh pr create
