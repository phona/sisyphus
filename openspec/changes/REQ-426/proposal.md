# REQ-426 — pr-ci-active-dispatch: sisyphus actively triggers ttpos-ci pipeline

## Problem

Business repos' `.github/workflows/dispatch.yml` only triggers on
`repository_dispatch` events — not on `pull_request` events. When the
analyze-agent opens a PR programmatically via `gh pr create` inside a Coder
workspace, no `repository_dispatch` is fired, so `ttpos-ci ci-go.yml` never
starts. The pr-ci-watch checker then polls indefinitely and eventually times
out with `no-gha`, routing to the verifier unnecessarily.

## Solution

Add a best-effort `_dispatch_ci_trigger()` call inside `_run_checker()` in
`create_pr_ci_watch.py`. After discovering the involved repos and before
starting the `watch_pr_ci` polling loop, sisyphus fires
`POST /repos/{owner}/{repo}/dispatches` with a configurable `event_type` for
each repo. Failure per-repo is logged as a warning and does not block polling.

Two new config flags gate and tune the behaviour:
- `pr_ci_dispatch_enabled` (default `False`) — feature flag for safe rollout
- `pr_ci_dispatch_event_type` (default `"ci-trigger"`) — must match the
  `on.repository_dispatch.types` value in the business repo's `dispatch.yml`

This change is purely an orchestrator operation (not a runner operation), using
`settings.github_token` which is already injected for incident reporting.

## Out of scope

- Modifying `checkers/pr_ci_watch.py` — the checker stays a pure read-only poller
- Any changes to runner pod or BKD agent paths
