# gh-incident-open: per-involved-repo loop

## MODIFIED Requirements

### Requirement: open_incident posts to the configured GitHub repo when a REQ escalates

The orchestrator SHALL expose a `gh_incident.open_incident` async function that
accepts an explicit `repo: str` kwarg. When `repo` and `settings.github_token` are
both non-empty, it MUST POST to `https://api.github.com/repos/{repo}/issues` with
a Bearer token, returning the resulting `html_url`. The function MUST return
`None` (and skip the POST) when either `repo` or `settings.github_token` is empty,
and MUST return `None` on any HTTP error without raising.

#### Scenario: GHI-S1 disabled when repo argument is empty
- **GIVEN** any `settings.github_token` value
- **WHEN** `open_incident(repo="", req_id="REQ-1", reason="x", retry_count=0, intent_issue_id="i", failed_issue_id="i", project_id="p")` is called
- **THEN** no HTTP request is made and the return value is `None`

#### Scenario: GHI-S2 disabled when github_token is empty
- **GIVEN** `settings.github_token = ""`
- **WHEN** `open_incident(repo="phona/sisyphus", ...)` is called
- **THEN** no HTTP request is made and the return value is `None`

#### Scenario: GHI-S3 success returns the html_url from the GitHub response
- **GIVEN** `settings.github_token` is set and the GH API returns 201 with `{"html_url": "https://github.com/phona/sisyphus/issues/42"}`
- **WHEN** `open_incident(repo="phona/sisyphus", ...)` is called
- **THEN** the POST URL MUST equal `https://api.github.com/repos/phona/sisyphus/issues` with `Authorization: Bearer ...` and `Accept: application/vnd.github+json` headers
- **AND** the return value equals `"https://github.com/phona/sisyphus/issues/42"`

#### Scenario: GHI-S4 request body contains REQ id, reason, and BKD cross-references
- **GIVEN** the GH API is mocked to record the POST body
- **WHEN** `open_incident(repo="phona/sisyphus", req_id="REQ-9", reason="fixer-round-cap", retry_count=0, intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A", state="fixer-running")` is called
- **THEN** the JSON body MUST contain the substrings `REQ-9`, `fixer-round-cap`, `intent-1`, `vfy-3`, `proj-A`, and `fixer-running`
- **AND** the JSON `labels` array MUST contain both `sisyphus:incident` and `reason:fixer-round-cap`

#### Scenario: GHI-S5 HTTP failure returns None and does not raise
- **GIVEN** the GH API returns HTTP 503 for `repo="phona/sisyphus"`
- **WHEN** `open_incident(repo="phona/sisyphus", ...)` is called
- **THEN** the call MUST NOT raise
- **AND** the return value MUST be `None`

### Requirement: escalate action opens an incident on real-escalate and is idempotent under resume cycles

The `escalate` action MUST resolve the list of incident-target repos via the
ordered fallback documented in "Requirement: incident repo resolution layers"
and, for every repo not already present as a key in `ctx.gh_incident_urls`,
MUST call `gh_incident.open_incident(repo=...)`. Successfully returned URLs
MUST be persisted to `ctx.gh_incident_urls` (a `dict[str, str]` mapping
repo slug to `html_url`); the first newly minted URL in the call MUST also
be written to the legacy `ctx.gh_incident_url` for backward compatibility.
The auto-resume branch MUST NOT call `open_incident`. Per-repo HTTP
failures MUST NOT abort the loop or the escalate flow. The
`github-incident` BKD tag MUST be appended when at least one URL was
opened or already present in `ctx.gh_incident_urls`.

#### Scenario: GHI-S6 real-escalate with single involved repo opens one incident
- **GIVEN** `settings.github_token` is set, `ctx.involved_repos = ["phona/sisyphus"]`, `settings.gh_incident_repo = ""`, and `gh_incident.open_incident` is mocked to return `"https://github.com/phona/sisyphus/issues/42"`
- **AND** the escalate input is `body.event = "verify.escalate"` with `ctx.escalated_reason = "verifier-decision-escalate"` (non-transient)
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.open_incident` MUST be awaited exactly once with `repo="phona/sisyphus"`, `req_id`, `reason="verifier-decision-escalate"`, `intent_issue_id`, and `failed_issue_id` matching the input
- **AND** `req_state.update_context` MUST be invoked with `gh_incident_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/issues/42"}`, `gh_incident_url = "https://github.com/phona/sisyphus/issues/42"`, and a non-empty `gh_incident_opened_at`
- **AND** `bkd.merge_tags_and_update` MUST be called with `add` containing `escalated`, `reason:verifier-decision-escalate`, AND `github-incident`

#### Scenario: GHI-S7 idempotent: pre-existing ctx.gh_incident_urls skips the second POST
- **GIVEN** the same setup as GHI-S6
- **AND** `ctx.gh_incident_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/issues/42"}` is already set
- **WHEN** `escalate(...)` runs again (e.g. resume → re-escalate cycle)
- **THEN** `gh_incident.open_incident` MUST NOT be awaited
- **AND** `ctx.gh_incident_urls` MUST be preserved unchanged in the update
- **AND** the BKD tag merge MUST still include `github-incident`

#### Scenario: GHI-S8 auto-resume branch does not open an incident
- **GIVEN** the input is `body.event = "session.failed"`, `auto_retry_count = 0` (transient with budget remaining)
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.open_incident` MUST NOT be awaited
- **AND** `bkd.follow_up_issue` MUST be awaited (auto-resume continues as today)

#### Scenario: GHI-S9 GH failure does not abort the escalate flow
- **GIVEN** the same setup as GHI-S6 except `gh_incident.open_incident` returns `None` (GH outage)
- **WHEN** `escalate(...)` runs
- **THEN** `bkd.merge_tags_and_update` MUST still be awaited
- **AND** the action MUST return `{"escalated": True, ...}` as before
- **AND** the persisted ctx MUST NOT include `gh_incident_url` or a non-empty `gh_incident_urls`
- **AND** the BKD tag merge MUST NOT include `github-incident`

#### Scenario: GHI-S10 disabled (no involved repos and no fallback) does not break escalate
- **GIVEN** `ctx.involved_repos` is unset, all other layers are empty, and `settings.gh_incident_repo = ""`
- **WHEN** `escalate(...)` runs through the real-escalate branch
- **THEN** `gh_incident.open_incident` MUST NOT be awaited
- **AND** the action MUST return `{"escalated": True, ...}` exactly as before this change
- **AND** ctx MUST NOT be mutated with `gh_incident_url`, `gh_incident_urls`, or `gh_incident_opened_at`
- **AND** the BKD tag merge MUST NOT include `github-incident`

#### Scenario: GHI-S11 multi-repo REQ opens one incident per involved repo
- **GIVEN** `settings.github_token` is set, `ctx.involved_repos = ["phona/repo-a", "phona/repo-b"]`, `settings.gh_incident_repo = ""`, and `gh_incident.open_incident` is mocked to return `"https://github.com/phona/repo-a/issues/7"` for `repo="phona/repo-a"` and `"https://github.com/phona/repo-b/issues/3"` for `repo="phona/repo-b"`
- **AND** the escalate input is `body.event = "verify.escalate"` with `ctx.escalated_reason = "verifier-decision-escalate"`
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.open_incident` MUST be awaited exactly twice — once with `repo="phona/repo-a"` and once with `repo="phona/repo-b"`
- **AND** `req_state.update_context` MUST be invoked with `gh_incident_urls` containing both `{"phona/repo-a": "https://github.com/phona/repo-a/issues/7"}` and `{"phona/repo-b": "https://github.com/phona/repo-b/issues/3"}`
- **AND** the BKD tag merge MUST include `github-incident` exactly once

#### Scenario: GHI-S12 partial failure isolated: one repo fails, the other succeeds
- **GIVEN** `ctx.involved_repos = ["phona/repo-a", "phona/repo-b"]` and `gh_incident.open_incident` returns `None` for `repo="phona/repo-a"` (e.g. 403 — PAT lacks Issues:Write) but returns `"https://github.com/phona/repo-b/issues/3"` for `repo="phona/repo-b"`
- **WHEN** `escalate(...)` runs
- **THEN** the action MUST return `{"escalated": True, ...}`
- **AND** `ctx.gh_incident_urls` MUST equal `{"phona/repo-b": "https://github.com/phona/repo-b/issues/3"}` (failed repo absent)
- **AND** the BKD tag merge MUST include `github-incident` (one success suffices)

#### Scenario: GHI-S13 idempotent across multi-repo: only missing repos POSTed on re-entry
- **GIVEN** `ctx.involved_repos = ["phona/repo-a", "phona/repo-b"]` and `ctx.gh_incident_urls = {"phona/repo-a": "https://github.com/phona/repo-a/issues/7"}` (repo-a already opened in a prior escalate call)
- **AND** `gh_incident.open_incident` returns `"https://github.com/phona/repo-b/issues/3"` for `repo="phona/repo-b"`
- **WHEN** `escalate(...)` runs again
- **THEN** `gh_incident.open_incident` MUST be awaited exactly once with `repo="phona/repo-b"` (NOT for `phona/repo-a`)
- **AND** the persisted `gh_incident_urls` MUST contain both `phona/repo-a` (existing) and `phona/repo-b` (newly added) keys

## ADDED Requirements

### Requirement: incident repo resolution layers

The escalate action MUST resolve the list of incident-target repos via the
following ordered fallback (the first non-empty layer wins):

1. `ctx.intake_finalized_intent.involved_repos`
2. `ctx.involved_repos`
3. BKD intent issue tags of the form `repo:<org>/<name>`
4. `settings.default_involved_repos`
5. `[settings.gh_incident_repo]` (single-element list, only when non-empty) —
   last-resort single-inbox fallback for REQs whose involved repos are unknown
   (e.g. intake-stage failures pre-clone) and explicit "central triage queue"
   deployments

When all five layers are empty, the action MUST skip the GH incident loop
entirely (no HTTP) and the BKD tag merge MUST NOT include `github-incident`.

#### Scenario: GHI-S14 falls back to settings.gh_incident_repo when involved_repos empty
- **GIVEN** `ctx.involved_repos` is unset, `ctx.intake_finalized_intent` is missing, no `repo:` tags, `settings.default_involved_repos = []`, but `settings.gh_incident_repo = "phona/sisyphus"`, `settings.github_token` is set, and `gh_incident.open_incident` returns `"https://github.com/phona/sisyphus/issues/99"`
- **WHEN** `escalate(...)` runs (real-escalate path)
- **THEN** `gh_incident.open_incident` MUST be awaited exactly once with `repo="phona/sisyphus"`
- **AND** `ctx.gh_incident_urls` MUST equal `{"phona/sisyphus": "https://github.com/phona/sisyphus/issues/99"}`
- **AND** the BKD tag merge MUST include `github-incident`

#### Scenario: GHI-S15 layers 1-4 take precedence over settings.gh_incident_repo
- **GIVEN** `ctx.involved_repos = ["phona/repo-a"]` and `settings.gh_incident_repo = "phona/sisyphus"` (both set)
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.open_incident` MUST be awaited exactly once with `repo="phona/repo-a"` (NOT `phona/sisyphus`)
- **AND** `ctx.gh_incident_urls` keys MUST equal `{"phona/repo-a"}` (no `phona/sisyphus` entry)
