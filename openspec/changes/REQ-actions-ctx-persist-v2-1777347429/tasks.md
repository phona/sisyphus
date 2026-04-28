# REQ-actions-ctx-persist-v2-1777347429 Tasks

## Stage: contract / spec

- [x] author specs/ctx-persist/spec.md with scenarios
- [x] author specs/ctx-persist/contract.spec.yaml

## Stage: implementation

- [x] start_challenger.py: add update_context({challenger_issue_id}) in slug-hit path
- [x] start_challenger.py: add update_context({challenger_issue_id}) in main dispatch path
- [x] watchdog.py: add defensive skip for issue_id=None + policy.stuck_sec=None
- [x] audit all _STATE_ISSUE_KEY entries vs their action update_context calls (all others OK)
- [x] unit tests: test_start_challenger_writes_challenger_issue_id_to_ctx
- [x] unit tests: test_start_challenger_slug_hit_writes_ctx
- [x] unit tests: test_missing_issue_id_in_ctx_non_autonomous_escalates (update)
- [x] unit tests: test_missing_issue_id_in_ctx_autonomous_bounded_skips (new)

## Stage: PR

- [x] git push feat/REQ-actions-ctx-persist-v2-1777347429
- [x] gh pr create --label sisyphus
