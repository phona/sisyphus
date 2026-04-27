## ADDED Requirements

### Requirement: sisyphus actively dispatches repository_dispatch before polling

Sisyphus SHALL fire a `POST /repos/{owner}/{repo}/dispatches` request for each
discovered repo before starting the check-run polling loop when
`checker_pr_ci_watch_enabled=True` and `pr_ci_dispatch_enabled=True`.
The request body MUST contain `event_type` equal to
`settings.pr_ci_dispatch_event_type` and a `client_payload` object with at
least the fields `branch` (the `feat/REQ-x` branch name) and `req_id`.
Dispatch MUST use `settings.github_token` for authorization via
`Authorization: Bearer` header.

When `pr_ci_dispatch_enabled` is `False` (default), sisyphus MUST NOT make
any `POST /dispatches` call, and the polling loop MUST proceed as before.

#### Scenario: PRCIAD-S1 dispatch fires for each repo before polling starts

- **GIVEN** `pr_ci_dispatch_enabled=True` and two involved repos `owner/repo-a` and `owner/repo-b`
- **WHEN** `_run_checker()` is called
- **THEN** `POST /repos/owner/repo-a/dispatches` and `POST /repos/owner/repo-b/dispatches` are each called once with the configured `event_type` before `watch_pr_ci()` begins

#### Scenario: PRCIAD-S2 dispatch flag disabled — no POST issued

- **GIVEN** `pr_ci_dispatch_enabled=False`
- **WHEN** `_run_checker()` is called with two repos
- **THEN** no `POST /dispatches` request is issued; `watch_pr_ci()` is called directly

### Requirement: dispatch failure is best-effort and does not abort polling

The dispatch step MUST NOT raise an exception or return a failure result when
the `POST /dispatches` call fails for any individual repo. Any HTTP error or
network exception per repo SHALL be logged at WARNING level and the system MUST
continue to the next repo and then proceed to the polling loop. The system MUST
NOT skip polling even if all repos' dispatch calls fail.

#### Scenario: PRCIAD-S3 one repo dispatch fails — other repos unaffected, polling continues

- **GIVEN** `pr_ci_dispatch_enabled=True` and two repos; repo-a returns HTTP 422, repo-b returns HTTP 204
- **WHEN** `_dispatch_ci_trigger()` is called
- **THEN** a warning is logged for repo-a; repo-b dispatch succeeds; no exception is raised; `watch_pr_ci()` is called normally

#### Scenario: PRCIAD-S4 no github_token — dispatch returns 401 warning only

- **GIVEN** `pr_ci_dispatch_enabled=True` and `github_token=""` (empty)
- **WHEN** `_dispatch_ci_trigger()` is called
- **THEN** the request is sent (with `Authorization: Bearer ` empty header); any resulting HTTP error is logged as a warning only; no exception propagates
