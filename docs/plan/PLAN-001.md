# PLAN-001: ttpos-ci-direct-dispatch

**REQ**: REQ-447
**status**: implementing

## Context

`create_pr_ci_watch` checker path currently only polls GitHub check-runs passively.
When GitHub Actions are not triggered (webhook miss, branch not in workflow trigger list),
sisyphus waits 1800s before timing out with `no-gha` or `timeout`.

`phona/ttpos-ci` is the central CI repo that provides reusable GHA workflows.
Business repos reference these via `workflow_call`. Sisyphus does not currently send
any signal to this repo.

## Proposal

Before starting the check-run polling loop in `_run_checker`, optionally POST a
`repository_dispatch` event to a configurable CI repo. The dispatch is best-effort:
failure only logs a warning and does not block polling.

## Design

### New config fields (3)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `ci_dispatch_enabled` | `bool` | `False` | Feature flag |
| `ci_dispatch_repo` | `str` | `""` | Target repo, e.g. `phona/ttpos-ci` |
| `ci_dispatch_event_type` | `str` | `"pr-ci-run"` | repository_dispatch event_type |

### Dispatch flow

1. `_run_checker` calls `_dispatch_to_ci_repo` before `checker.watch_pr_ci`
2. `_dispatch_to_ci_repo`:
   - skips if `ci_dispatch_enabled=False` or `ci_dispatch_repo=""`
   - for each involved repo: calls `_get_pr_info` to get (pr_number, sha)
   - POSTs `POST /repos/{ci_dispatch_repo}/dispatches` with payload:
     ```json
     {
       "event_type": "<ci_dispatch_event_type>",
       "client_payload": {
         "req_id": "...", "source_repo": "...",
         "branch": "...", "sha": "...", "pr_number": N
       }
     }
     ```
   - HTTP error → log warning, continue (best-effort)
   - skips repos where PR lookup fails

### Token

Uses `settings.github_token` (same as polling path). For `repository_dispatch`,
the token needs `repo` scope (classic PAT) or `Contents: Read-and-write` (fine-grained)
on the target CI repo.

## Files Changed

1. `orchestrator/src/orchestrator/config.py`
2. `orchestrator/src/orchestrator/actions/create_pr_ci_watch.py`
3. `orchestrator/tests/test_actions_create_pr_ci_watch.py`
4. `docs/integration-contracts.md`
5. `openspec/changes/REQ-447/`

## Risks

- Token needs write scope on `phona/ttpos-ci`; mitigated by feature flag default=False
- Dispatch payload format must match ttpos-ci expectation; `ci_dispatch_event_type` is configurable
