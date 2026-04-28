# REQ-actions-ctx-persist-v2: fix start_challenger ctx write + watchdog defensive

## Problem

`start_challenger.py` dispatches a BKD issue but never calls `update_context` to write
`challenger_issue_id` into the REQ context. The watchdog uses
`_STATE_ISSUE_KEY[CHALLENGER_RUNNING] = "challenger_issue_id"` to reconcile the BKD
session status. With no value in ctx, the watchdog treats the absent key as "session ended"
and escalates after `ended_sec=300` even when the challenger is still actively running.

Observed symptom: challenger.pass arrived 2.5 min into the run, but watchdog had already
emitted `SESSION_FAILED` at 5 min mark, escalating a live REQ.

Secondary issue: `create_accept` is a K8s direct exec (no BKD issue), so `accept_issue_id`
is never written. Watchdog similarly misreads absent key as "session ended" for ACCEPT_RUNNING.

## Root Cause

`start_challenger.py` lines 85-87 (pre-fix):
```python
await dispatch_slugs.put(pool, slug, issue.id)
log.info("start_challenger.done", req_id=req_id, issue_id=issue.id)
return {"challenger_issue_id": issue.id, "req_id": req_id}
```

The function returns the `challenger_issue_id` in its return dict (which goes to engine result),
but never calls `req_state.update_context` to persist it into the DB context row.
The watchdog reads from `req_state.context`, not from the action return value.

Compare with correct pattern in `invoke_verifier` (lines 161-165):
```python
await dispatch_slugs.put(pool, slug, issue.id)
await req_state.update_context(pool, req_id, {"verifier_issue_id": issue.id, ...})
```

## Solution

### Fix 1: start_challenger.py — write ctx in both paths

Add `await req_state.update_context(pool, req_id, {"challenger_issue_id": issue.id})`
after `dispatch_slugs.put` in the main path, and before return in the slug-hit path.
Both paths must write ctx to handle idempotent replay correctly.

### Fix 2: watchdog.py — defensive skip for missing issue_id on autonomous-bounded stages

When `issue_id is None AND policy.stuck_sec is None` (autonomous-bounded stages like
CHALLENGER_RUNNING, ACCEPT_RUNNING, ARCHIVING), skip rather than treating absence of
ctx key as "session ended". Log a warning to surface the condition for monitoring.

This is defense-in-depth: even if an action forgets `update_context`, watchdog won't
blindly kill stages that might still be running.

Stages with `stuck_sec != None` (e.g. PR_CI_RUNNING with stuck_sec=14400,
SPEC_LINT_RUNNING with stuck_sec=300) are unaffected by this check and still escalate
correctly via the `ended_sec` path.

## Scope

- `orchestrator/src/orchestrator/actions/start_challenger.py`
- `orchestrator/src/orchestrator/watchdog.py`
- `orchestrator/tests/test_actions_start_challenger.py`
- `orchestrator/tests/test_watchdog.py`
