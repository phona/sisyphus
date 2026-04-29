## Stage: contract / spec
- [x] author proposal.md
- [x] author specs/bkd-status-sync/spec.md

## Stage: implementation
- [x] webhook.py: add exponential backoff retry to _push_upstream_status (max 3 attempts)
- [x] watchdog.py: add _sync_stuck_sub_agent_statuses_tick compensation cleanup
- [x] watchdog.py: integrate compensation into run_loop (every 5 ticks)

## Stage: test
- [x] test_webhook_upstream_done.py: add retry success / retry exhausted tests
- [x] test_watchdog_bkd_sync.py: add 9 compensation scenario tests
- [x] run all webhook + watchdog tests (65 passed)

## Stage: PR
- [ ] git push feat/REQ-fix-bkd-sub-issue-status-sync-1777426309
- [ ] gh pr create with sisyphus label
