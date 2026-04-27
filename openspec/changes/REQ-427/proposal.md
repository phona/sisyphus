# REQ-427: Dispatch Idempotency by Slug

## Problem

Every call to `bkd.create_issue()` in orchestrator action handlers is non-idempotent.
When a webhook event triggers an action that calls `create_issue()` and the process crashes
after the BKD POST succeeds but before `mark_processed` is set, BKD retries the same event.
The webhook dedup layer returns `"retry"` and the action runs again — creating a duplicate BKD issue.

The crash window: `create_issue()` succeeded → `update_context()` or `mark_processed()` not yet called.

## Solution

Add a `dispatch_slugs` Postgres table that stores `slug → issue_id` mappings. Before each
`create_issue()` call, compute a deterministic slug and check the table:

- Slug found: return existing `issue_id` (skip creation)
- Slug absent: create issue, insert slug mapping, then proceed as normal

**Slug scheme:**
- `invoke_verifier`: `verifier|{req_id}|{stage}|{trigger}|r{fixer_round}`
- `start_fixer`: `fixer|{req_id}|{fixer}|r{next_round}`
- Action handlers with `body`: `{action}|{req_id}|{body.executionId or ""}`

Using `executionId` for one-shot actions ensures crash+retry (same executionId = same slug)
is caught, while a legitimately new invocation (new executionId from new webhook event) is not.

## Scope

- New: `orchestrator/migrations/0010_dispatch_slugs.sql`
- New: `orchestrator/src/orchestrator/store/dispatch_slugs.py`
- Modified: 8 action files that call `bkd.create_issue()`
- New: unit tests for the store module
