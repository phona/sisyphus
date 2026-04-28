# Proposal: PR Merged → Orchestrator Archive Closed Loop

**REQ**: REQ-pr-merge-archive-hook-1777344443

## Problem

After `pr_ci_watch` passes, a REQ enters `PENDING_USER_REVIEW` waiting for a BKD
`statusId=done` signal. When a human reviewer merges the PR on GitHub, sisyphus has
no awareness — the REQ stays stuck in `pending-user-review` indefinitely.

## Solution (Option A)

1. **GHA workflow** (`.github/workflows/sisyphus-pr-merged-hook.yml`): fires on
   `pull_request[closed]` where `merged==true` and label `sisyphus` is present.
   Extracts `REQ-<id>` from the branch name, then POSTs to the orch admin endpoint.

2. **New admin endpoint** `POST /admin/req/{req_id}/pr-merged`: authenticates with
   the same Bearer token as other admin endpoints. Writes merge metadata to REQ
   context, then emits `PR_MERGED` event through the state machine.

3. **State machine extension**: new `Event.PR_MERGED` with transitions from
   `PENDING_USER_REVIEW`, `REVIEW_RUNNING`, and `PR_CI_RUNNING` → `ARCHIVING`
   via `done_archive` action. CAS guarantees concurrency safety.

## Scope

- New event + transitions in `state.py`
- New endpoint in `admin.py`
- New GHA workflow in `.github/workflows/`
- Unit tests in `orchestrator/tests/test_admin_pr_merged.py`

## Out of Scope

- PR ready-for-review notification (separate REQ)
- Human PR rollback reverse flow
- Changes to `done_archive` action internals
