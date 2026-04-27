# REQ-447: ttpos-ci-direct-dispatch

## Problem

`pr-ci-watch` currently relies on GitHub Actions being passively triggered by the `pull_request`
webhook event in business repos. When that trigger is missed (webhook delivery failure, branch not
in workflow trigger list, or `phona/ttpos-ci` workflow expecting an explicit dispatch), sisyphus
waits up to 1800 seconds before timing out with a `no-gha` or `timeout` verdict.

## Solution

Add a feature-flagged "direct dispatch" step: when entering `pr-ci-watch`, sisyphus proactively
POSTs a `repository_dispatch` event to a configurable central CI repo (defaulting to
`phona/ttpos-ci`) for each involved source repo. This ensures CI runs even when webhook-based
triggering is unreliable or the CI setup explicitly expects a dispatch from the orchestrator.

## Key Design Decisions

- **Best-effort**: dispatch failure never blocks the polling loop
- **Feature flag `ci_dispatch_enabled` (default False)**: safe opt-in rollout
- **One dispatch per source repo**: allows ttpos-ci to run repo-specific CI jobs
- **Standard payload**: `{req_id, source_repo, branch, sha, pr_number}` — ttpos-ci can use
  whichever fields it needs
- **Token reuse**: uses the existing `settings.github_token`; no new secret needed; operator
  must ensure token has write scope on `ci_dispatch_repo`

## Out of Scope

- Changes to how check-runs are polled (unchanged)
- Any changes to `phona/ttpos-ci` itself
- Retry logic for dispatch (best-effort semantics)
