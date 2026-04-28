# REQ-router-session-completed-audit-1777344435: fix escalated_reason="session-completed"

## Problem

15 REQs in the last 7 days were escalated with `context->>'escalated_reason' = 'session-completed'`.
This value is meaningless for triage: it only tells you the trigger event type, not _why_ the
escalation happened.

Root cause: `escalate.py` resolves its `reason` string via a 4-level priority chain. When called
with `body.event = "session.completed"` and no `ctx.escalated_reason` pre-set, the priority-4
fallback produces `"session-completed"` from `body.event.replace(".", "-")`.

`webhook.py` step 5.8 pre-set `escalated_reason` only for `VERIFY_ESCALATE`. Two other
session.completed → escalate paths were unguarded:

- `INTAKE_FAIL` (intake-agent session + result:fail, or INTAKE_PASS downgraded when no
  finalized intent JSON found in assistant messages)
- `PR_CI_TIMEOUT` (pr-ci-watch session completed with pr-ci:timeout tag)

Secondary audit found `router.derive_event` for `session.completed` is already correct:
known stage tags without a result tag all return `None` (silently skipped). No router change
needed for the "stage tag without result" path.

## Proposal

Extend webhook.py step 5.8 from a single VERIFY_ESCALATE guard to a table-driven lookup
covering all three session.completed → escalate routes:

| Event            | escalated_reason     |
|------------------|----------------------|
| VERIFY_ESCALATE  | "verifier-decision"  |
| INTAKE_FAIL      | "intake-fail"        |
| PR_CI_TIMEOUT    | "pr-ci-timeout"      |

Add 5 missing `derive_event` test cases to confirm the "no result tag" paths return `None`
rather than SESSION_FAILED.

## Impact

- Metabase Q* queries filtering `reason = 'session-completed'` will now see the correct value.
- Historically escalated REQs keep their stored `session-completed` reason (no backfill).
- No state machine change; no new escalation paths introduced.
