# ttpos-ci-direct-dispatch spec

## ADDED Requirements

### Requirement: orchestrator proactively dispatches repository_dispatch before polling

When `ci_dispatch_enabled=True` and `ci_dispatch_repo` is non-empty, the orchestrator
SHALL POST a `repository_dispatch` event to `ci_dispatch_repo` for each involved source
repo before entering the pr-ci-watch polling loop. The dispatch MUST use the configured
`ci_dispatch_event_type` and include a `client_payload` with `req_id`, `source_repo`,
`branch`, `sha`, and `pr_number`. Dispatch failures MUST NOT block the polling loop
(best-effort semantics): HTTP errors or missing PRs are logged as warnings only.

#### Scenario: SIS-447-S1 dispatch is skipped when ci_dispatch_enabled is False

- **GIVEN** `ci_dispatch_enabled=False`
- **WHEN** `create_pr_ci_watch` enters the checker path
- **THEN** no `POST /repos/{ci_dispatch_repo}/dispatches` is sent
- **AND** watch_pr_ci proceeds normally

#### Scenario: SIS-447-S2 dispatch is skipped when ci_dispatch_repo is empty

- **GIVEN** `ci_dispatch_enabled=True` and `ci_dispatch_repo=""`
- **WHEN** `create_pr_ci_watch` enters the checker path
- **THEN** no HTTP request is made to any dispatch endpoint
- **AND** watch_pr_ci proceeds normally

#### Scenario: SIS-447-S3 one dispatch per source repo when enabled

- **GIVEN** `ci_dispatch_enabled=True`, `ci_dispatch_repo="phona/ttpos-ci"`,
  and two source repos `["phona/repo-a", "phona/repo-b"]`
- **WHEN** both repos have open PRs and `create_pr_ci_watch` runs
- **THEN** two `POST /repos/phona/ttpos-ci/dispatches` requests are sent, one per repo
- **AND** each payload contains `source_repo`, `branch`, `sha`, `pr_number`, `req_id`

#### Scenario: SIS-447-S4 dispatch HTTP error does not block polling

- **GIVEN** `ci_dispatch_enabled=True` and the dispatch endpoint returns HTTP 422
- **WHEN** `create_pr_ci_watch` runs
- **THEN** the error is logged as a warning
- **AND** `watch_pr_ci` still runs and returns its normal verdict

#### Scenario: SIS-447-S5 dispatch is skipped for repos with no open PR

- **GIVEN** `ci_dispatch_enabled=True` and one source repo has no open PR
- **WHEN** `create_pr_ci_watch` runs
- **THEN** no dispatch is sent for that repo
- **AND** the warning is logged
- **AND** watch_pr_ci runs normally
