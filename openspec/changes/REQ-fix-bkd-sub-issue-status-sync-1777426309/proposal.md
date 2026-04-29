# Proposal: Fix unreliable BKD sub-agent issue status synchronization

## Problem

In the sisyphus orchestrator, when sub-agent issues (analyze, challenger, fixer, accept, done-archive) complete their work, the BKD statusId synchronization to "done" is unreliable:

1. `_push_upstream_status` in `webhook.py` executes only once (fire-and-forget). Exceptions are only logged at warning level without retry.
2. If the PATCH fails (network jitter, BKD 5xx, race condition with BKD auto status changes), the sub-agent issue remains stuck in "review" forever.
3. Subsequent state transitions (ANALYZE_DONE -> artifact check -> spec lint -> ...) do not go back to sync previously completed sub-agent issues.

This causes the BKD kanban "review" column to be polluted with large numbers of false-positive completed issues, leading users to believe there are many problems requiring manual intervention.

## Solution

Combined approach A + B:

- **A (webhook retry)**: Add exponential backoff retry (max 3 attempts) to `_push_upstream_status`, maximizing the chance of first-sync success.
- **B (watchdog compensation)**: Add a periodic compensation task to the watchdog loop that scans BKD for `sessionStatus=completed` + `statusId=review` sub-agent issues and auto-PATCHes them to `done`.

## Scope

- `orchestrator/src/orchestrator/webhook.py` — `_push_upstream_status` retry
- `orchestrator/src/orchestrator/watchdog.py` — compensation cleanup task
- `orchestrator/tests/test_webhook_upstream_done.py` — retry tests
- `orchestrator/tests/test_watchdog_bkd_sync.py` — new compensation tests

## Out of scope

- Prompt templates (not touched)
- State machine transition table (not touched)
- Action behavior (not touched)
- Business repo integration (not touched)
