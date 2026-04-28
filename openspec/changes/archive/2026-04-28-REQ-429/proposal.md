# REQ-429: admin /escalate kind param + BKD auto-sync

## Problem

`POST /admin/req/{req_id}/escalate` bypasses the normal state machine to force a REQ
into ESCALATED. However, it never syncs BKD: the intent issue keeps its old status and
gains no `escalated` tag, so the kanban board is blind to admin-forced escalations.
Additionally, the escalation reason is hard-coded to `"admin"`, losing context when the
operator knows the actual cause (e.g. `"infra-flake"`, `"watchdog-stuck"`).

## Solution

1. Add optional `kind` body field (default `"admin"`) — written to `ctx.escalated_reason`
   and used as the BKD `reason:<kind>` tag.
2. After SQL UPDATE + runner cleanup task, sync BKD intent issue:
   - `merge_tags_and_update(add=["escalated", "reason:<kind>"], status_id="review")`
3. BKD sync is non-blocking (wrapped in try/except + warn log), matching the pattern
   used in `escalate.py` session-failed path.

## Scope

Single file change: `orchestrator/src/orchestrator/admin.py`.
New tests in `orchestrator/tests/test_admin.py` (EKS-S1 through EKS-S6).
