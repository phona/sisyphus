# REQ-426 tasks — pr-ci-active-dispatch

## Stage: spec

- [x] author openspec/changes/REQ-426/proposal.md
- [x] author openspec/changes/REQ-426/specs/pr-ci-active-dispatch/spec.md with scenarios

## Stage: implementation

- [x] config.py: add pr_ci_dispatch_enabled and pr_ci_dispatch_event_type flags
- [x] create_pr_ci_watch.py: add _dispatch_ci_trigger() helper
- [x] create_pr_ci_watch.py: call _dispatch_ci_trigger() in _run_checker() before watch_pr_ci

## Stage: unit tests

- [x] test_actions_create_pr_ci_watch.py: test_dispatch_ci_trigger_calls_gh_api
- [x] test_actions_create_pr_ci_watch.py: test_dispatch_ci_trigger_tolerates_per_repo_error
- [x] test_actions_create_pr_ci_watch.py: test_run_checker_skips_dispatch_when_disabled

## Stage: PR

- [x] git push feat/REQ-426
- [x] gh pr create
