# issue-pr-link

## ADDED Requirements

### Requirement: ensure_pr_links_in_ctx MUST return cached PrLink list when ctx.pr_links is set

The helper MUST first parse the cached pr_links list out of ctx and SHALL
return that parsed PrLink list directly without issuing any GitHub HTTP
request, runner exec call, or BKD PATCH whenever the list is non-empty and
contains at least one well-formed entry. The cache-hit short-circuit keeps
repeat calls O(1) and avoids ratepressing the GitHub PAT — sisyphus creates
4-8 BKD issues per REQ, so an uncached fan-out would multiply the GH calls
per stage transition.

#### Scenario: LP-S1 cache hit returns cached without GH call

- **GIVEN** `ctx = {"pr_links": [{"repo":"phona/sisyphus","number":42,"url":"https://github.com/phona/sisyphus/pull/42"}]}`
- **AND** an `httpx.AsyncClient` mock that fails the test if any HTTP request is made
- **WHEN** `ensure_pr_links_in_ctx(req_id="REQ-x", branch="feat/REQ-x", ctx=ctx, project_id="p")` is invoked
- **THEN** the return value MUST equal `[PrLink(repo="phona/sisyphus", number=42, url="https://github.com/phona/sisyphus/pull/42")]`
- **AND** no `httpx` request, no `k8s_runner.exec_in_runner` call, and no `req_state.update_context` call MUST occur


### Requirement: ensure_pr_links_in_ctx MUST discover via runner + GH REST and persist to ctx on cache miss

When `ctx.pr_links` is missing or empty, the helper SHALL run repo discovery
in the runner pod (`for d in /workspace/source/*/; do git -C "$d" remote get-url origin; done`),
parse the stdout into `owner/repo` slugs, and for each slug issue
`GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=open` against
`https://api.github.com`. The first PR returned per repo MUST be wrapped into
a `PrLink(repo, pr.number, pr.html_url)`. If at least one `PrLink` is collected,
the helper MUST persist the list as `ctx.pr_links = [link.to_dict(), ...]`
via `req_state.update_context` so subsequent callsites hit the cache.

#### Scenario: LP-S2 cache miss runs discovery and stashes ctx

- **GIVEN** `ctx = {}` and `_discover_repos_via_runner` returns `["phona/sisyphus"]`
- **AND** GH `/repos/phona/sisyphus/pulls?head=phona:feat/REQ-x&state=open` returns
  `[{"number": 42, "html_url": "https://github.com/phona/sisyphus/pull/42"}]`
- **WHEN** `ensure_pr_links_in_ctx(req_id="REQ-x", branch="feat/REQ-x", ctx={}, project_id="p")` is invoked
- **THEN** the return value MUST equal `[PrLink(repo="phona/sisyphus", number=42, url="https://github.com/phona/sisyphus/pull/42")]`
- **AND** `req_state.update_context` MUST be called once with patch
  `{"pr_links": [{"repo":"phona/sisyphus","number":42,"url":"https://github.com/phona/sisyphus/pull/42"}]}`


### Requirement: ensure_pr_links_in_ctx MUST tolerate runner discovery failure

The helper MUST NOT propagate exceptions from the runner exec path. When
`k8s_runner.get_controller()` raises `RuntimeError` (no controller in dev env),
or when `exec_in_runner` raises any exception, the helper SHALL log a warning
and return an empty list. The caller MUST be able to continue creating BKD
issues without `pr:*` tags this round; a subsequent callsite gets another
chance once the runner is back.

#### Scenario: LP-S3 runner exec error returns empty without raising

- **GIVEN** `ctx = {}` and `k8s_runner.get_controller().exec_in_runner` raises
  `RuntimeError("pod not found")`
- **WHEN** `ensure_pr_links_in_ctx` is invoked
- **THEN** no exception MUST propagate
- **AND** the return value MUST equal `[]`
- **AND** `req_state.update_context` MUST NOT be called
- **AND** a warning MUST be logged with `req_id` and `error` fields


### Requirement: discover_pr_links MUST tolerate per-repo GH API errors

The helper MUST NOT abort multi-repo discovery when a single repo's GitHub
request fails. For each repo, `httpx.HTTPError` (5xx, 404, network errors)
SHALL be caught locally and logged at WARNING level, the helper SHALL continue
to the next repo, and the final return value SHALL contain only the PR links
that were successfully collected. An empty result is allowed (caller treats
as no-op).

#### Scenario: LP-S4 one repo errors, another succeeds

- **GIVEN** `_discover_repos_via_runner` returns `["phona/repo-a", "phona/repo-b"]`
- **AND** GH `/repos/phona/repo-a/pulls` returns HTTP 503
- **AND** GH `/repos/phona/repo-b/pulls` returns
  `[{"number": 7, "html_url": "https://github.com/phona/repo-b/pull/7"}]`
- **WHEN** `discover_pr_links(req_id="REQ-x", branch="feat/REQ-x")` is invoked
- **THEN** the return value MUST contain exactly one `PrLink` for `phona/repo-b#7`
- **AND** no entry for `phona/repo-a` MUST be present
- **AND** a warning log MUST be emitted with `repo="phona/repo-a"`


### Requirement: ensure_pr_links_in_ctx MUST backfill known issue ids on first discovery

The helper SHALL backfill `pr:*` tags onto every sisyphus-tracked BKD issue id
present in ctx the first time it transitions from cache-miss to a non-empty
discovered list. The helper SHALL inspect the keys `analyze_issue_id`,
`staging_test_issue_id`, `pr_ci_watch_issue_id`, `accept_issue_id`, and
`archive_issue_id`, and for each non-empty string id MUST call
`bkd.merge_tags_and_update(project_id, issue_id, add=pr_link_tags(links))`.
The backfill MUST tolerate per-issue PATCH errors by logging a warning and
continuing to the next id. This covers the analyze issue (created before
PRs exist) and any earlier sub-issue that was created before the first
successful discovery window.

#### Scenario: LP-S5 first discovery backfills analyze issue

- **GIVEN** `ctx = {"analyze_issue_id": "abc123"}`
- **AND** discovery yields `[PrLink("phona/sisyphus", 42, "https://github.com/phona/sisyphus/pull/42")]`
- **WHEN** `ensure_pr_links_in_ctx` is invoked
- **THEN** `bkd.merge_tags_and_update` MUST be called once with arguments
  `(project_id="p", issue_id="abc123", add=["pr:phona/sisyphus#42"])`
- **AND** `req_state.update_context` MUST be called with `pr_links` patch
- **AND** the return value MUST equal `[PrLink("phona/sisyphus", 42, ...)]`


### Requirement: pr_link_tags MUST render PrLinks as pr:owner/repo#N strings

`pr_link_tags(links)` MUST emit a list whose order matches the input list and
whose i-th entry equals `f"pr:{links[i].repo}#{links[i].number}"`. The format
is the public contract callers depend on for tag construction; changing it
breaks existing BKD issue tag schemes.

#### Scenario: LP-S6 pr_link_tags formats two repos

- **GIVEN** `links = [PrLink("phona/sisyphus", 42, "..."), PrLink("phona/runner", 7, "...")]`
- **WHEN** `pr_link_tags(links)` is invoked
- **THEN** the return value MUST equal `["pr:phona/sisyphus#42", "pr:phona/runner#7"]`


### Requirement: from_ctx MUST tolerate malformed cached entries

The `pr_links.from_ctx(ctx)` parser MUST defensively skip entries that are not
dicts or are missing required keys (`repo`, `number`), or whose `number` field
cannot coerce to int. Malformed entries MUST NOT raise — the parser SHALL
return whatever well-formed entries it could read. This guards against ctx
written by an older sisyphus version drifting forward.

#### Scenario: LP-S7 from_ctx returns only well-formed entries

- **GIVEN** `ctx = {"pr_links": [
    {"repo":"phona/sisyphus","number":42,"url":"u1"},
    {"repo":"missing-num"},
    "not-a-dict",
    {"repo":"phona/runner","number":"7","url":"u2"}
  ]}`
- **WHEN** `from_ctx(ctx)` is invoked
- **THEN** the return value MUST equal
  `[PrLink("phona/sisyphus", 42, "u1"), PrLink("phona/runner", 7, "u2")]`
- **AND** no exception MUST propagate
