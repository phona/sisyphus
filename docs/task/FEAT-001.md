# FEAT-001 Add active CI dispatch before pr-ci-watch polling

- **status**: in_progress
- **priority**: P1
- **owner**: sisyphus-bot
- **createdAt**: 2026-04-27 00:00

## Description

Before sisyphus starts polling GitHub check-runs in the `pr-ci-watch` stage, it
should actively fire a `repository_dispatch` event on each involved repo to
trigger the business repo's `dispatch.yml` → `ttpos-ci ci-go.yml` pipeline.

Currently the `dispatch.yml` in ttpos repos only triggers on `repository_dispatch`
events (not on PR open), so CI may never start automatically when an analyze-agent
opens a PR programmatically.

Acceptance criteria:
- `_dispatch_ci_trigger()` helper added to `create_pr_ci_watch.py`
- Feature-gated by `pr_ci_dispatch_enabled` (default `False`)
- `pr_ci_dispatch_event_type` config for the event type to fire (default `"ci-trigger"`)
- Best-effort: dispatch failure logs a warning and does not block polling
- Unit tests cover success, per-repo failure, and flag-disabled cases

## ActiveForm

Implementing active CI dispatch before pr-ci-watch polling.

## Dependencies

- **blocked by**: (none)
- **blocks**: (none)

## Notes

Related plan: PLAN-001
