# TASK-001: ttpos-ci-direct-dispatch

**REQ**: REQ-447
**status**: in_progress
**owner**: analyze-agent (fld9vdxg)

## Goal

Add a feature-flagged capability for sisyphus orchestrator to proactively POST a
`repository_dispatch` event to a configurable central CI repo (default: `phona/ttpos-ci`)
before entering the pr-ci-watch polling loop.

## Scope

- `orchestrator/src/orchestrator/config.py`: 3 new settings
- `orchestrator/src/orchestrator/actions/create_pr_ci_watch.py`: `_dispatch_to_ci_repo` helper
- `orchestrator/tests/test_actions_create_pr_ci_watch.py`: unit tests for dispatch
- `docs/integration-contracts.md`: §6 footnote on dispatch
- `openspec/changes/REQ-447/`: spec, proposal, tasks
