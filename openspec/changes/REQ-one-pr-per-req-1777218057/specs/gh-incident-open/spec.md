# gh-incident-open delta — REQ-one-pr-per-req-1777218057

## ADDED Requirements

### Requirement: comment_on_pr posts a comment on a specific PR when called

The orchestrator SHALL expose a `gh_incident.comment_on_pr` async function that
accepts explicit `repo: str`, `pr_number: int`, plus the same identification
kwargs as `open_incident` (`req_id`, `reason`, `retry_count`,
`intent_issue_id`, `failed_issue_id`, `project_id`, `state`). When `repo` and
`settings.github_token` are both non-empty and `pr_number` is positive, the
function MUST POST to
`https://api.github.com/repos/{repo}/issues/{pr_number}/comments` with a Bearer
token and a JSON body whose `body` field contains the same `_format_body`
metadata `open_incident` uses (REQ id, reason, retry count, BKD project,
intent issue id, failed sub-issue id, opened-at). The function MUST return the
response `html_url` on success and `None` (without raising) on any HTTP error,
on 2xx without `html_url`, or when `repo` / `pr_number` / `github_token` is
missing.

#### Scenario: COP-S1 disabled when repo argument is empty
- **GIVEN** `settings.github_token = "ghp_xxx"`
- **WHEN** `comment_on_pr(repo="", pr_number=42, req_id="REQ-1", reason="x", retry_count=0, intent_issue_id="i", failed_issue_id="i", project_id="p")` is called
- **THEN** no HTTP request is made
- **AND** the return value is `None`

#### Scenario: COP-S2 disabled when github_token is empty
- **GIVEN** `settings.github_token = ""`
- **WHEN** `comment_on_pr(repo="phona/sisyphus", pr_number=42, ...)` is called
- **THEN** no HTTP request is made
- **AND** the return value is `None`

#### Scenario: COP-S3 disabled when pr_number is zero or negative
- **GIVEN** `settings.github_token = "ghp_xxx"`
- **WHEN** `comment_on_pr(repo="phona/sisyphus", pr_number=0, ...)` is called
- **THEN** no HTTP request is made
- **AND** the return value is `None`

#### Scenario: COP-S4 success returns the comment html_url
- **GIVEN** `settings.github_token` is set and the GH API returns 201 with `{"html_url": "https://github.com/phona/sisyphus/pull/42#issuecomment-99"}`
- **WHEN** `comment_on_pr(repo="phona/sisyphus", pr_number=42, ...)` is called
- **THEN** the POST URL MUST equal `https://api.github.com/repos/phona/sisyphus/issues/42/comments`
- **AND** the request headers MUST include `Authorization: Bearer ghp_xxx` and `Accept: application/vnd.github+json`
- **AND** the return value MUST equal `"https://github.com/phona/sisyphus/pull/42#issuecomment-99"`

#### Scenario: COP-S5 request body contains REQ id, reason, and BKD cross-references
- **GIVEN** the GH API is mocked to record the POST body
- **WHEN** `comment_on_pr(repo="phona/sisyphus", pr_number=42, req_id="REQ-9", reason="fixer-round-cap", retry_count=1, intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A", state="fixer-running")` is called
- **THEN** the JSON `body` field MUST contain the substrings `REQ-9`, `fixer-round-cap`, `intent-1`, `vfy-3`, `proj-A`, and `fixer-running`

#### Scenario: COP-S6 HTTP failure returns None and does not raise
- **GIVEN** the GH API returns HTTP 503 for `repo="phona/sisyphus"` `pr_number=42`
- **WHEN** `comment_on_pr(...)` is called
- **THEN** the call MUST NOT raise
- **AND** the return value MUST be `None`

#### Scenario: COP-S7 network error returns None and does not raise
- **GIVEN** the underlying httpx client raises `ConnectError`
- **WHEN** `comment_on_pr(...)` is called
- **THEN** the call MUST NOT raise
- **AND** the return value MUST be `None`

### Requirement: find_pr_for_branch resolves a PR number for a (repo, branch)

The orchestrator SHALL expose a `gh_incident.find_pr_for_branch` async
function that, given a `repo` slug and `branch` name, queries
`GET https://api.github.com/repos/{repo}/pulls?head={owner}:{branch}&state=all&per_page=5`
and returns the `number` of the first PR returned, or `None` when the API
returns an empty list. The function MUST return `None` (without raising) when
`repo` or `settings.github_token` is empty, and MUST return `None` on any HTTP
error.

#### Scenario: FPR-S1 disabled when repo or token is empty
- **GIVEN** `settings.github_token = ""` (or `repo = ""`)
- **WHEN** `find_pr_for_branch(repo="phona/sisyphus", branch="feat/REQ-x")` is called
- **THEN** no HTTP request is made
- **AND** the return value is `None`

#### Scenario: FPR-S2 returns first PR number when API responds with at least one PR
- **GIVEN** the GH API returns 200 with `[{"number": 42, "html_url": "..."}, {"number": 39}]` for `head=phona:feat/REQ-x`
- **WHEN** `find_pr_for_branch(repo="phona/sisyphus", branch="feat/REQ-x")` is called
- **THEN** the GET URL MUST equal `https://api.github.com/repos/phona/sisyphus/pulls` with query `head=phona:feat/REQ-x`, `state=all`, `per_page=5`
- **AND** the return value MUST equal `42`

#### Scenario: FPR-S3 returns None when API returns empty list
- **GIVEN** the GH API returns 200 with `[]`
- **WHEN** `find_pr_for_branch(repo="phona/sisyphus", branch="feat/REQ-x")` is called
- **THEN** the return value MUST be `None`

#### Scenario: FPR-S4 returns None on HTTP error
- **GIVEN** the GH API returns HTTP 503
- **WHEN** `find_pr_for_branch(...)` is called
- **THEN** the call MUST NOT raise
- **AND** the return value MUST be `None`

## MODIFIED Requirements

### Requirement: escalate action opens an incident on real-escalate and is idempotent under resume cycles

The `escalate` action MUST resolve the list of incident-target repos via the
ordered fallback documented in "Requirement: incident repo resolution layers"
and, for every repo not already present as a key in `ctx.gh_incident_urls`,
MUST first call `gh_incident.find_pr_for_branch(repo=..., branch="feat/{REQ}")`
to look up the existing PR number for the REQ's feat branch. When a PR number
is returned, the action MUST call
`gh_incident.comment_on_pr(repo=..., pr_number=...)` to post the incident
metadata as a PR comment. When `find_pr_for_branch` returns `None` (no PR yet
for this REQ on this repo), the action MUST fall back to
`gh_incident.open_incident(repo=...)` exactly as before. Successfully returned
URLs MUST be persisted to `ctx.gh_incident_urls` (a `dict[str, str]` mapping
repo slug to `html_url`); the kind of artifact landed (`"comment"` or
`"issue"`) MUST be persisted to a sibling `ctx.gh_incident_kinds` dict. The
first newly minted URL in the call MUST also be written to the legacy
`ctx.gh_incident_url` for backward compatibility. The auto-resume branch MUST
NOT call `find_pr_for_branch`, `comment_on_pr`, or `open_incident`. Per-repo
HTTP failures MUST NOT abort the loop or the escalate flow. The
`github-incident` BKD tag MUST be appended when at least one URL was opened or
already present in `ctx.gh_incident_urls`, regardless of whether the URL is a
PR comment or a fresh issue.

#### Scenario: GHI-S6 real-escalate single involved repo posts a PR comment when PR exists
- **GIVEN** `settings.github_token` is set, `ctx.involved_repos = ["phona/sisyphus"]`, `settings.gh_incident_repo = ""`
- **AND** `gh_incident.find_pr_for_branch(repo="phona/sisyphus", branch="feat/REQ-9")` is mocked to return `42`
- **AND** `gh_incident.comment_on_pr` is mocked to return `"https://github.com/phona/sisyphus/pull/42#issuecomment-99"`
- **AND** the escalate input is `body.event = "verify.escalate"` with `ctx.escalated_reason = "verifier-decision-escalate"` (non-transient)
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.find_pr_for_branch` MUST be awaited exactly once with `repo="phona/sisyphus"`, `branch="feat/REQ-9"`
- **AND** `gh_incident.comment_on_pr` MUST be awaited exactly once with `repo="phona/sisyphus"`, `pr_number=42`, `req_id="REQ-9"`, `reason="verifier-decision-escalate"`, `intent_issue_id`, and `failed_issue_id` matching the input
- **AND** `gh_incident.open_incident` MUST NOT be awaited
- **AND** `req_state.update_context` MUST be invoked with `gh_incident_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/42#issuecomment-99"}`, `gh_incident_kinds = {"phona/sisyphus": "comment"}`, `gh_incident_url = "https://github.com/phona/sisyphus/pull/42#issuecomment-99"`, and a non-empty `gh_incident_opened_at`
- **AND** `bkd.merge_tags_and_update` MUST be called with `add` containing `escalated`, `reason:verifier-decision-escalate`, AND `github-incident`

#### Scenario: GHI-S7 idempotent: pre-existing ctx.gh_incident_urls skips the second POST
- **GIVEN** the same setup as GHI-S6
- **AND** `ctx.gh_incident_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/42#issuecomment-99"}` is already set
- **WHEN** `escalate(...)` runs again (e.g. resume → re-escalate cycle)
- **THEN** `gh_incident.find_pr_for_branch` MUST NOT be awaited
- **AND** `gh_incident.comment_on_pr` MUST NOT be awaited
- **AND** `gh_incident.open_incident` MUST NOT be awaited
- **AND** `ctx.gh_incident_urls` MUST be preserved unchanged in the update
- **AND** the BKD tag merge MUST still include `github-incident`

#### Scenario: GHI-S8 auto-resume branch does not interact with GitHub
- **GIVEN** the input is `body.event = "session.failed"`, `auto_retry_count = 0` (transient with budget remaining)
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.find_pr_for_branch`, `gh_incident.comment_on_pr`, and `gh_incident.open_incident` MUST NOT be awaited
- **AND** `bkd.follow_up_issue` MUST be awaited (auto-resume continues as today)

#### Scenario: GHI-S9 GH failure does not abort the escalate flow
- **GIVEN** the same setup as GHI-S6 except `gh_incident.comment_on_pr` returns `None` (GH outage / 4xx / 5xx)
- **WHEN** `escalate(...)` runs
- **THEN** `bkd.merge_tags_and_update` MUST still be awaited
- **AND** the action MUST return `{"escalated": True, ...}` as before
- **AND** the persisted ctx MUST NOT include `gh_incident_url` or a non-empty `gh_incident_urls`
- **AND** the BKD tag merge MUST NOT include `github-incident`

#### Scenario: GHI-S10 disabled (no involved repos and no fallback) does not break escalate
- **GIVEN** `ctx.involved_repos` is unset, all other layers are empty, and `settings.gh_incident_repo = ""`
- **WHEN** `escalate(...)` runs through the real-escalate branch
- **THEN** `gh_incident.find_pr_for_branch`, `gh_incident.comment_on_pr`, and `gh_incident.open_incident` MUST NOT be awaited
- **AND** the action MUST return `{"escalated": True, ...}` exactly as before this change
- **AND** ctx MUST NOT be mutated with `gh_incident_url`, `gh_incident_urls`, `gh_incident_kinds`, or `gh_incident_opened_at`
- **AND** the BKD tag merge MUST NOT include `github-incident`

#### Scenario: GHI-S11 multi-repo REQ posts one comment per involved repo
- **GIVEN** `settings.github_token` is set, `ctx.involved_repos = ["phona/repo-a", "phona/repo-b"]`, `settings.gh_incident_repo = ""`
- **AND** `gh_incident.find_pr_for_branch` returns `7` for `repo="phona/repo-a"` and `3` for `repo="phona/repo-b"`
- **AND** `gh_incident.comment_on_pr` returns `"https://github.com/phona/repo-a/pull/7#issuecomment-1"` for `(repo="phona/repo-a", pr_number=7)` and `"https://github.com/phona/repo-b/pull/3#issuecomment-2"` for `(repo="phona/repo-b", pr_number=3)`
- **AND** the escalate input is `body.event = "verify.escalate"` with `ctx.escalated_reason = "verifier-decision-escalate"`
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.comment_on_pr` MUST be awaited exactly twice — once with `repo="phona/repo-a"`, `pr_number=7`, and once with `repo="phona/repo-b"`, `pr_number=3`
- **AND** `gh_incident.open_incident` MUST NOT be awaited
- **AND** `req_state.update_context` MUST be invoked with `gh_incident_urls` containing both `"phona/repo-a"` and `"phona/repo-b"` keys mapped to their respective `html_url`
- **AND** `gh_incident_kinds` MUST be `{"phona/repo-a": "comment", "phona/repo-b": "comment"}`
- **AND** the BKD tag merge MUST include `github-incident` exactly once

#### Scenario: GHI-S12 partial failure isolated: one repo fails, the other succeeds
- **GIVEN** `ctx.involved_repos = ["phona/repo-a", "phona/repo-b"]`
- **AND** `gh_incident.find_pr_for_branch` returns `7` for `repo="phona/repo-a"` and `3` for `repo="phona/repo-b"`
- **AND** `gh_incident.comment_on_pr` returns `None` for `(repo="phona/repo-a", pr_number=7)` (e.g. 403 — PAT lacks Issues:Write) but returns `"https://github.com/phona/repo-b/pull/3#issuecomment-2"` for `(repo="phona/repo-b", pr_number=3)`
- **WHEN** `escalate(...)` runs
- **THEN** the action MUST return `{"escalated": True, ...}`
- **AND** `ctx.gh_incident_urls` MUST equal `{"phona/repo-b": "https://github.com/phona/repo-b/pull/3#issuecomment-2"}` (failed repo absent)
- **AND** `ctx.gh_incident_kinds` MUST equal `{"phona/repo-b": "comment"}` (failed repo absent)
- **AND** the BKD tag merge MUST include `github-incident` (one success suffices)

#### Scenario: GHI-S13 idempotent across multi-repo: only missing repos POSTed on re-entry
- **GIVEN** `ctx.involved_repos = ["phona/repo-a", "phona/repo-b"]` and `ctx.gh_incident_urls = {"phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1"}` (repo-a already commented in a prior escalate call)
- **AND** `gh_incident.find_pr_for_branch` returns `3` for `repo="phona/repo-b"`
- **AND** `gh_incident.comment_on_pr` returns `"https://github.com/phona/repo-b/pull/3#issuecomment-2"` for `(repo="phona/repo-b", pr_number=3)`
- **WHEN** `escalate(...)` runs again
- **THEN** `gh_incident.find_pr_for_branch` MUST be awaited exactly once with `repo="phona/repo-b"` (NOT for `phona/repo-a`)
- **AND** `gh_incident.comment_on_pr` MUST be awaited exactly once with `repo="phona/repo-b"`, `pr_number=3`
- **AND** the persisted `gh_incident_urls` MUST contain both `phona/repo-a` (existing) and `phona/repo-b` (newly added) keys

## ADDED Requirements

### Requirement: escalate falls back to opening a fresh issue when no PR exists for the feat branch

The escalate action MUST fall back to `gh_incident.open_incident(repo=..., ...)`
to create a fresh GitHub issue when the layered involved-repos resolver yields
a repo for which `gh_incident.find_pr_for_branch(repo=..., branch="feat/{REQ}")`
returns `None` (no PR has been opened yet — typical for escalations during
INTAKING / early ANALYZING that fire before the analyze-agent has pushed a
branch, or for deployments whose `gh_incident_repo` fallback points at a
triage-inbox repo without per-REQ PRs). The persisted `ctx.gh_incident_kinds`
entry for that repo MUST be `"issue"`, and the URL persisted in
`ctx.gh_incident_urls` MUST be the issue `html_url` returned by
`open_incident`. The fallback MUST NOT swallow exceptions thrown by the
resolver layers above it — only PR-lookup and comment-POST HTTP errors are
absorbed by returning `None`. The `github-incident` BKD tag semantics are
unchanged: appended when at least one URL (comment or issue) was minted or
already present in ctx.

#### Scenario: ICP-S1 falls back to issue when no PR exists for feat/{REQ}
- **GIVEN** `settings.github_token` is set, `ctx.involved_repos = ["phona/sisyphus"]`
- **AND** `gh_incident.find_pr_for_branch(repo="phona/sisyphus", branch="feat/REQ-9")` is mocked to return `None`
- **AND** `gh_incident.open_incident` is mocked to return `"https://github.com/phona/sisyphus/issues/42"`
- **AND** the escalate input is `body.event = "verify.escalate"` with `ctx.escalated_reason = "verifier-decision-escalate"`
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.find_pr_for_branch` MUST be awaited exactly once
- **AND** `gh_incident.comment_on_pr` MUST NOT be awaited
- **AND** `gh_incident.open_incident` MUST be awaited exactly once with `repo="phona/sisyphus"`, `req_id="REQ-9"`, `reason="verifier-decision-escalate"`
- **AND** `req_state.update_context` MUST be invoked with `gh_incident_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/issues/42"}` and `gh_incident_kinds = {"phona/sisyphus": "issue"}`
- **AND** the BKD tag merge MUST include `github-incident`

#### Scenario: ICP-S2 mixed multi-repo: comment for one repo, issue fallback for another
- **GIVEN** `settings.github_token` is set, `ctx.involved_repos = ["phona/repo-a", "phona/repo-b"]`
- **AND** `gh_incident.find_pr_for_branch` returns `7` for `repo="phona/repo-a"` and `None` for `repo="phona/repo-b"` (no feat branch pushed yet on repo-b)
- **AND** `gh_incident.comment_on_pr(repo="phona/repo-a", pr_number=7, ...)` returns `"https://github.com/phona/repo-a/pull/7#issuecomment-1"`
- **AND** `gh_incident.open_incident(repo="phona/repo-b", ...)` returns `"https://github.com/phona/repo-b/issues/42"`
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.comment_on_pr` MUST be awaited exactly once with `repo="phona/repo-a"`
- **AND** `gh_incident.open_incident` MUST be awaited exactly once with `repo="phona/repo-b"`
- **AND** `ctx.gh_incident_urls` MUST equal `{"phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1", "phona/repo-b": "https://github.com/phona/repo-b/issues/42"}`
- **AND** `ctx.gh_incident_kinds` MUST equal `{"phona/repo-a": "comment", "phona/repo-b": "issue"}`
- **AND** the BKD tag merge MUST include `github-incident` exactly once

#### Scenario: ICP-S3 PR-lookup HTTP error treated as no-PR (falls back to issue)
- **GIVEN** `settings.github_token` is set, `ctx.involved_repos = ["phona/sisyphus"]`
- **AND** `gh_incident.find_pr_for_branch(repo="phona/sisyphus", ...)` returns `None` (the function absorbs the underlying HTTP error and reports "not found")
- **AND** `gh_incident.open_incident` returns `"https://github.com/phona/sisyphus/issues/42"`
- **WHEN** `escalate(...)` runs
- **THEN** `gh_incident.open_incident` MUST be awaited exactly once with `repo="phona/sisyphus"`
- **AND** `ctx.gh_incident_kinds["phona/sisyphus"]` MUST equal `"issue"`
- **AND** the action MUST return `{"escalated": True, ...}`
