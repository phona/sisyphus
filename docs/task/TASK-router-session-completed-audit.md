# TASK-router-session-completed-audit: fix escalated_reason="session-completed" fallback

**REQ**: REQ-router-session-completed-audit
**status**: in_progress
**owner**: claude

## Goal

Fix 15 incidents where `context->>'escalated_reason' = 'session-completed'` appears in
`req_state`, caused by `escalate.py` falling back to `body.event.replace(".", "-")` when
`ctx.escalated_reason` is not pre-set and `body.event = "session.completed"`.

## Root Cause

`webhook.py` step 5.8 pre-sets `escalated_reason` only for `Event.VERIFY_ESCALATE`.
Two other `session.completed` → escalate paths are unguarded:

- `INTAKE_FAIL` (intake-agent session.completed + result:fail, or INTAKE_PASS downgrade
  when no finalized intent JSON found)
- `PR_CI_TIMEOUT` (pr-ci-watch session.completed with `pr-ci:timeout` tag)

When escalate is called with `body.event = "session.completed"` and no `ctx.escalated_reason`,
`escalate.py` priority-4 fallback yields `"session-completed"` — ambiguous and unhelpful.

`router.derive_event` is already correct: stage tag without result → None → skipped silently.
No router.py change needed.

## Scope

1. `orchestrator/src/orchestrator/webhook.py` — extend step 5.8 from VERIFY_ESCALATE-only
   to a table covering VERIFY_ESCALATE + INTAKE_FAIL + PR_CI_TIMEOUT.
2. `orchestrator/tests/test_router.py` — add 4 missing coverage cases:
   - challenger without result → None
   - fixer without result → FIXER_DONE
   - no stage tag in session.completed → None
   - known stage tag + result:weird → None
