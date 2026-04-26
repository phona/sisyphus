# gh-incident-open Specification

## Purpose
TBD - created by archiving change REQ-impl-gh-incident-open-1777173133. Update Purpose after archive.
## Requirements
### Requirement: open_incident posts to the configured GitHub repo when a REQ escalates

The orchestrator SHALL expose a `gh_incident.open_incident` async function that, when
both `settings.gh_incident_repo` and `settings.github_token` are non-empty, MUST POST
to `https://api.github.com/repos/{owner}/{repo}/issues` with a Bearer token,
returning the resulting `html_url`. The function MUST return `None` (and skip the
POST) when either setting is empty, and MUST return `None` on any HTTP error
without raising.

#### Scenario: GHI-S1 disabled when gh_incident_repo is empty
- **GIVEN** `settings.gh_incident_repo = ""` and any `github_token`
- **WHEN** `open_incident(req_id="REQ-1", reason="x", retry_count=0, intent_issue_id="i", failed_issue_id="i", project_id="p")` is called
- **THEN** no HTTP request is made and the return value is `None`

#### Scenario: GHI-S2 disabled when github_token is empty
- **GIVEN** `settings.gh_incident_repo = "phona/sisyphus"` and `settings.github_token = ""`
- **WHEN** `open_incident(...)` is called
- **THEN** no HTTP request is made and the return value is `None`

#### Scenario: GHI-S3 success returns the html_url from the GitHub response
- **GIVEN** `gh_incident_repo = "phona/sisyphus"`, `github_token` is set, and the GH API returns 201 with `{"html_url": "https://github.com/phona/sisyphus/issues/42"}`
- **WHEN** `open_incident(...)` is called
- **THEN** the POST URL is `https://api.github.com/repos/phona/sisyphus/issues` with `Authorization: Bearer ...` and `Accept: application/vnd.github+json` headers
- **AND** the return value equals `"https://github.com/phona/sisyphus/issues/42"`

#### Scenario: GHI-S4 request body contains REQ id, reason, and BKD cross-references
- **GIVEN** the GH API is mocked to record the POST body
- **WHEN** `open_incident(req_id="REQ-9", reason="fixer-round-cap", retry_count=0, intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A", state="fixer-running")` is called
- **THEN** the JSON body MUST contain the substrings `REQ-9`, `fixer-round-cap`, `intent-1`, `vfy-3`, `proj-A`, and `fixer-running`
- **AND** the JSON `labels` array MUST contain both `sisyphus:incident` and `reason:fixer-round-cap`

#### Scenario: GHI-S5 HTTP failure returns None and does not raise
- **GIVEN** the GH API returns HTTP 503
- **WHEN** `open_incident(...)` is called
- **THEN** the call MUST NOT raise
- **AND** the return value MUST be `None`

### Requirement: escalate action opens an incident on real-escalate and is idempotent under resume cycles

The `escalate` action MUST call `gh_incident.open_incident` in the "real escalate"
branch (after `bkd.merge_tags_and_update`), MUST persist the returned URL to
`ctx.gh_incident_url`, and MUST NOT post a second incident when re-invoked with
`ctx.gh_incident_url` already set. The auto-resume branch MUST NOT call
`open_incident`. GH failures MUST NOT abort the escalate flow.

#### Scenario: GHI-S6 real-escalate path opens an incident and stores the URL in ctx
- **GIVEN** `settings.gh_incident_repo = "phona/sisyphus"`, `settings.github_token` is set, and `gh_incident.open_incident` is mocked to return `"https://github.com/phona/sisyphus/issues/42"`
- **AND** the escalate input is `body.event = "verify.escalate"` with `ctx.escalated_reason = "verifier-decision-escalate"` (non-transient)
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.open_incident` MUST be awaited exactly once with `req_id`, `reason="verifier-decision-escalate"`, `intent_issue_id`, and `failed_issue_id` matching the input
- **AND** `req_state.update_context` MUST be invoked with `gh_incident_url = "https://github.com/phona/sisyphus/issues/42"` and a non-empty `gh_incident_opened_at`
- **AND** `bkd.merge_tags_and_update` MUST be called with `add` containing `escalated`, `reason:verifier-decision-escalate`, AND `github-incident`

#### Scenario: GHI-S7 idempotent: pre-existing ctx.gh_incident_url skips the second POST
- **GIVEN** the same setup as GHI-S6
- **AND** `ctx.gh_incident_url = "https://github.com/phona/sisyphus/issues/42"` is already set
- **WHEN** `escalate(...)` runs again (e.g. resume → re-escalate cycle)
- **THEN** `gh_incident.open_incident` MUST NOT be awaited
- **AND** the existing `ctx.gh_incident_url` is preserved

#### Scenario: GHI-S8 auto-resume branch does not open an incident
- **GIVEN** the input is `body.event = "session.failed"`, `auto_retry_count = 0` (transient with budget remaining)
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.open_incident` MUST NOT be awaited
- **AND** `bkd.follow_up_issue` IS awaited (auto-resume continues as today)

#### Scenario: GHI-S9 GH failure does not abort the escalate flow
- **GIVEN** the same setup as GHI-S6
- **BUT** `gh_incident.open_incident` returns `None` (GH outage)
- **WHEN** `escalate(...)` runs
- **THEN** `bkd.merge_tags_and_update` MUST still be awaited
- **AND** the action returns `{"escalated": True, ...}` as before
- **AND** `ctx` MUST NOT receive `gh_incident_url`

#### Scenario: GHI-S10 disabled (gh_incident_repo empty) does not break escalate
- **GIVEN** `settings.gh_incident_repo = ""` (default)
- **WHEN** `escalate(...)` runs through the real-escalate branch
- **THEN** the action returns `{"escalated": True, ...}` exactly as before this change
- **AND** ctx is NOT mutated with `gh_incident_url`
- **AND** the BKD tag merge MUST NOT include `github-incident`

