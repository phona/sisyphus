## ADDED Requirements

### Requirement: links.bkd_issue_url helper renders clickable BKD frontend URLs

The orchestrator SHALL expose a `links.bkd_issue_url(project_id, issue_id)`
helper that MUST return a clickable BKD frontend URL of the form
`<frontend>/projects/<project_id>/issues/<issue_id>`. The `<frontend>` base
is resolved in this order:

1. `settings.bkd_frontend_url` when non-empty (explicit override; trailing
   `/` MUST be stripped).
2. `settings.bkd_base_url` with a trailing `/api` stripped (the production
   default has the form `https://.../api`).

The helper MUST return `None` when either `project_id` or `issue_id` is empty
or the resolved frontend base is empty / malformed (no scheme).

#### Scenario: XLINK-S1 base url with /api suffix derives frontend

- **GIVEN** `settings.bkd_base_url = "https://bkd.example/api"` and
  `settings.bkd_frontend_url = ""`
- **WHEN** `links.bkd_issue_url("p", "i")` is called
- **THEN** the result equals `"https://bkd.example/projects/p/issues/i"`

#### Scenario: XLINK-S2 explicit frontend url override beats base url

- **GIVEN** `settings.bkd_base_url = "https://api.bkd.example/api"` and
  `settings.bkd_frontend_url = "https://bkd.example/"`
- **WHEN** `links.bkd_issue_url("p", "i")` is called
- **THEN** the result equals `"https://bkd.example/projects/p/issues/i"`

#### Scenario: XLINK-S3 missing identifiers return None

- **GIVEN** any `settings`
- **WHEN** `links.bkd_issue_url("", "x")` or `links.bkd_issue_url("p", "")`
  is called
- **THEN** the result MUST be `None`

#### Scenario: XLINK-S4 unparseable bkd_base_url returns None when override empty

- **GIVEN** `settings.bkd_base_url = "not-a-url"` and
  `settings.bkd_frontend_url = ""`
- **WHEN** `links.bkd_issue_url("p", "i")` is called
- **THEN** the result MUST be `None`

### Requirement: links.format_pr_links_md renders markdown bullet links per PR

The orchestrator SHALL expose `links.format_pr_links_md(pr_urls)` that MUST
return a list of markdown bullet strings of the form
`"- [<repo>#<n>](<html_url>)"` for each `(repo, html_url)` pair in
`pr_urls`. The PR number `<n>` is parsed as the trailing digits of the
URL path; URLs without a parseable `/pull/<n>` segment MUST fall back to
`"- [<repo>](<html_url>)"`. The function MUST return an empty list when
`pr_urls` is None, empty, or not a dict.

#### Scenario: XLINK-S5 multi-repo dict produces sorted bullet list

- **GIVEN** `pr_urls = {"foo/bar": "https://github.com/foo/bar/pull/9",
  "baz/qux": "https://github.com/baz/qux/pull/3"}`
- **WHEN** `links.format_pr_links_md(pr_urls)` is called
- **THEN** the result equals `["- [baz/qux#3](https://github.com/baz/qux/pull/3)",
  "- [foo/bar#9](https://github.com/foo/bar/pull/9)"]`
  (sorted by repo for stable output)

#### Scenario: XLINK-S6 empty / None / non-dict input returns empty list

- **GIVEN** `pr_urls` is `None`, `{}`, or `"not-a-dict"`
- **WHEN** `links.format_pr_links_md(pr_urls)` is called
- **THEN** the result MUST equal `[]`

### Requirement: webhook persists bkd_intent_url on REQ first observation

The webhook handler SHALL include `bkd_intent_url` (computed via
`links.bkd_issue_url(project_id, intent_issue_id)`) in the initial
`req_state.insert_init` context dict on first observation of a REQ,
alongside the existing `intent_issue_id` and `intent_title`. The field
MUST be omitted from the dict when `bkd_issue_url` returns `None` (no
deployment configured); existing context fields are not mutated.

#### Scenario: XLINK-S7 fresh REQ insert_init includes bkd_intent_url

- **GIVEN** a webhook payload for a previously unseen REQ with `projectId="P"`
  and `issueId="I"`, `settings.bkd_base_url = "https://bkd.example/api"`
- **WHEN** the webhook handler reaches the `req_state.insert_init` branch
- **THEN** the `context` arg passed to `insert_init` MUST contain
  `"bkd_intent_url": "https://bkd.example/projects/P/issues/I"`
- **AND** it MUST also retain `intent_issue_id = "I"` and `intent_title`

#### Scenario: XLINK-S8 helper returning None omits the field

- **GIVEN** `settings.bkd_base_url = "not-a-url"` and
  `settings.bkd_frontend_url = ""`
- **WHEN** the webhook initialises a fresh REQ
- **THEN** the `context` arg passed to `insert_init` MUST NOT contain a
  `bkd_intent_url` key

### Requirement: create_pr_ci_watch action persists pr_urls to req_state context

`actions.create_pr_ci_watch.create_pr_ci_watch` SHALL invoke
`links.discover_pr_urls(repos, branch)` once, before the checker / BKD-agent
dispatch fork, and persist any non-empty result via
`req_state.update_context(pool, req_id, {"pr_urls": <dict>})`. The action
MUST proceed to its existing dispatch path regardless of discovery outcome
(empty result, GH outage, missing repos list — all are non-blocking).

#### Scenario: XLINK-S9 successful discovery persists dict and continues to checker dispatch

- **GIVEN** `discover_pr_urls` is mocked to return
  `{"foo/bar": "https://github.com/foo/bar/pull/9"}` and
  `settings.checker_pr_ci_watch_enabled = True`
- **WHEN** `create_pr_ci_watch(body=..., req_id="REQ-x", tags=[], ctx={...})`
  runs
- **THEN** `req_state.update_context` MUST be invoked with patch
  `{"pr_urls": {"foo/bar": "https://github.com/foo/bar/pull/9"}}`
- **AND** the underlying `_run_checker` path MUST be entered exactly once

#### Scenario: XLINK-S10 empty discovery does not call update_context

- **GIVEN** `discover_pr_urls` is mocked to return `{}`
- **WHEN** `create_pr_ci_watch` runs
- **THEN** `req_state.update_context` MUST NOT be called with a `pr_urls`
  patch (other ctx writes during checker run are unaffected)

#### Scenario: XLINK-S11 discovery exception does not abort dispatch

- **GIVEN** `discover_pr_urls` raises `httpx.HTTPError`
- **WHEN** `create_pr_ci_watch` runs
- **THEN** the action MUST NOT propagate the exception
- **AND** the underlying dispatch path MUST still be entered exactly once

### Requirement: gh_incident body embeds clickable BKD intent and PR links

`gh_incident.open_incident` SHALL accept two new optional kwargs:
`bkd_intent_url: str | None = None` and `pr_urls: dict[str, str] | None = None`.
The POST body produced by `_format_body` MUST:

- When `bkd_intent_url` is non-empty, include the markdown link
  `[BKD intent issue](<bkd_intent_url>)` on the same line as the existing
  raw-id field. The raw id MUST also remain present so the substring
  contracts (GHI-S4) keep matching.
- When `pr_urls` is non-empty, include a section beginning
  `**PRs**:` followed by the comma-separated list of markdown links
  produced by `links.format_pr_links_md(pr_urls)` (joined inline rather
  than as bullet list, to keep the issue body compact).

When either kwarg is absent / None / empty, the body MUST be byte-identical
to today's pre-cross-link body for that field (no orphan headers, no empty
PR section).

#### Scenario: XLINK-S12 body contains markdown link to BKD intent when url provided

- **GIVEN** `open_incident(... intent_issue_id="i-1",
  bkd_intent_url="https://bkd.example/projects/p/issues/i-1", ...)` is
  called and the GH API returns 201
- **WHEN** the POST body is captured
- **THEN** the body string MUST contain the literal substring
  `[BKD intent issue](https://bkd.example/projects/p/issues/i-1)`
- **AND** the body MUST also still contain the raw id `i-1` (preserves
  existing GHI-S4 contract)

#### Scenario: XLINK-S13 body contains PR markdown links when pr_urls provided

- **GIVEN** `open_incident(... pr_urls={"foo/bar":
  "https://github.com/foo/bar/pull/9"}, ...)` is called
- **WHEN** the POST body is captured
- **THEN** the body MUST contain `**PRs**:` followed on the same line by
  `[foo/bar#9](https://github.com/foo/bar/pull/9)`

#### Scenario: XLINK-S14 absent kwargs do not add PR section

- **GIVEN** `open_incident(...)` is called without `pr_urls` (or with
  `pr_urls=None` / `pr_urls={}`)
- **WHEN** the POST body is captured
- **THEN** the body MUST NOT contain the literal `**PRs**:` substring

### Requirement: escalate action forwards bkd_intent_url and pr_urls from ctx to open_incident

`actions.escalate.escalate` SHALL read `bkd_intent_url` and `pr_urls` from
`ctx` (best-effort: missing keys are passed as `None` / `{}`) and forward
them as kwargs to every `gh_incident.open_incident` call. No new ctx field
is written by escalate for cross-link purposes (the upstream actions are
authoritative writers).

#### Scenario: XLINK-S15 escalate threads ctx fields through to open_incident

- **GIVEN** `ctx = {... "involved_repos": ["foo/bar"], "bkd_intent_url":
  "https://bkd.example/projects/p/issues/i", "pr_urls": {"foo/bar":
  "https://github.com/foo/bar/pull/9"}}` and a real-escalate path
- **WHEN** `escalate(...)` runs and calls `open_incident`
- **THEN** the captured `open_incident` kwargs for `repo="foo/bar"` MUST
  contain `bkd_intent_url="https://bkd.example/projects/p/issues/i"` and
  `pr_urls={"foo/bar": "https://github.com/foo/bar/pull/9"}`

### Requirement: analyze prompt enforces a sisyphus cross-link footer in every PR body

`prompts/analyze.md.j2` SHALL receive a `bkd_intent_issue_url` template
variable from `start_analyze` and render a "PR body footer" section that
instructs the analyze-agent to append a fixed block to the body of every
PR it opens. The block MUST contain:

- the literal HTML comment marker `<!-- sisyphus:cross-link -->`
- the REQ id
- the markdown link to the BKD intent issue (when `bkd_intent_issue_url`
  is non-empty)

The marker comment is fixed so downstream tooling can detect a
sisyphus-managed PR by string match.

#### Scenario: XLINK-S16 analyze prompt renders cross-link block when url provided

- **GIVEN** `render("analyze.md.j2", req_id="REQ-x", project_id="P",
  issue_id="I", bkd_intent_issue_url="https://bkd.example/projects/P/issues/I",
  cloned_repos=[], aissh_server_id="X", project_alias="P")` is called
- **WHEN** the template is rendered
- **THEN** the output MUST contain the literal substring
  `<!-- sisyphus:cross-link -->`
- **AND** the output MUST contain
  `[BKD intent issue](https://bkd.example/projects/P/issues/I)`

#### Scenario: XLINK-S17 analyze prompt omits link line when bkd_intent_issue_url empty

- **GIVEN** the same render call but `bkd_intent_issue_url=""`
- **WHEN** the template is rendered
- **THEN** the output MUST still contain `<!-- sisyphus:cross-link -->`
  and the REQ id, but MUST NOT contain `[BKD intent issue](`

### Requirement: done_archive prompt renders pr_urls when present

`actions.done_archive.done_archive` SHALL pass `pr_urls` from ctx to the
`done_archive.md.j2` render. The template MUST render any non-empty
`pr_urls` value as a markdown bullet list under a "Known PRs" heading,
using `links.format_pr_links_md`. When `pr_urls` is empty / missing the
template MUST NOT render the heading (no orphan section).

#### Scenario: XLINK-S18 prompt renders Known PRs bullets when ctx has pr_urls

- **GIVEN** `done_archive` action is invoked with `ctx = {... "pr_urls":
  {"foo/bar": "https://github.com/foo/bar/pull/9"}}`
- **WHEN** the template is rendered
- **THEN** the output MUST contain the substring `## Known PRs` followed
  by a `- [foo/bar#9](https://github.com/foo/bar/pull/9)` line

#### Scenario: XLINK-S19 prompt omits heading when pr_urls absent

- **GIVEN** `done_archive` is invoked with `ctx = {}` (no pr_urls)
- **WHEN** the template is rendered
- **THEN** the output MUST NOT contain `## Known PRs`

### Requirement: active-req Metabase query exposes clickable URL columns

`observability/queries/sisyphus/05-active-req-overview.sql` SHALL select
two additional columns alongside the existing `req_id`, `state`, etc:

- `bkd_intent_url` from `r.context->>'bkd_intent_url'` (raw URL string;
  Metabase column type "URL" or "Markdown" makes it clickable).
- `pr_urls_md` computed from `r.context->'pr_urls'` jsonb, rendered as a
  newline-separated string of markdown bullet links so a Metabase
  "Markdown" column type renders them clickable. NULL when the jsonb is
  null / empty.

#### Scenario: XLINK-S20 query returns both new columns

- **GIVEN** `req_state` row with `context = '{"intent_issue_id":"I",
  "bkd_intent_url":"https://bkd.example/projects/P/issues/I",
  "pr_urls":{"foo/bar":"https://github.com/foo/bar/pull/9"}}'::jsonb`
- **WHEN** the SQL in `05-active-req-overview.sql` is executed
- **THEN** the result row MUST have `bkd_intent_url =
  "https://bkd.example/projects/P/issues/I"`
- **AND** `pr_urls_md` MUST contain the substring
  `[foo/bar#9](https://github.com/foo/bar/pull/9)`

#### Scenario: XLINK-S21 query tolerates missing context fields

- **GIVEN** `req_state` row with `context = '{}'::jsonb`
- **WHEN** the SQL is executed
- **THEN** `bkd_intent_url` MUST be NULL
- **AND** `pr_urls_md` MUST be NULL (or empty string)
