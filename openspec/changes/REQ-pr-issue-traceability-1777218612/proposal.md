# feat: clickable cross-link PR <-> issue <-> BKD

## Why

A single sisyphus REQ surfaces in three different places:

1. **GitHub PR** — `feat/<REQ>` branch on each involved source repo, opened by analyze-agent.
2. **GitHub issue** — incident issue opened by `gh_incident.open_incident` when the REQ enters ESCALATED.
3. **BKD issue** — agent-execution sessions on the BKD board (intake / analyze / accept / fixer / etc).

Today the operator has to copy raw IDs between tabs to navigate. Concrete pain:

- The GH incident body says `**BKD intent issue**: \`p2ouk4kg\`` — not a URL. Operators paste the id into the BKD UI by hand. `req_state.context.intent_issue_id` exists, but the BKD frontend URL is never assembled.
- PR descriptions written by analyze-agent freeform sometimes mention REQ id, sometimes don't, never include a clickable link to the BKD session that produced them. There is no PR-body template / contract.
- `req_state.context` knows the BKD issue ids (`intent_issue_id`, `accept_issue_id`, `pr_ci_watch_issue_id`, `archive_issue_id`) but never persists the GitHub PR URLs — `pr_ci_watch` already calls `_get_pr_info` which returns `html_url` but the URL is dropped.
- Metabase Q05 (`05-active-req-overview.sql`) shows `req_id` and `state` but no clickable navigation. Drilling from the dashboard into a REQ requires a separate tab juggle.

The three artifacts already exist; the gap is bidirectional clickable links between them.

## What Changes

Single new capability `cross-link` covering URL helpers + persistence + four
embedding sites. No state-machine change, no new BKD agent, no new check.

- **New module** `orchestrator/src/orchestrator/links.py`
  - `bkd_issue_url(project_id, issue_id) -> str | None` — frontend URL derived
    by stripping a trailing `/api` from `settings.bkd_base_url`, with explicit
    `settings.bkd_frontend_url` override.
  - `format_pr_links_md(pr_urls: dict[str, str] | None) -> list[str]` —
    markdown bullets `- [<repo>#<n>](<html_url>)`.
- **New config** `settings.bkd_frontend_url` (default empty → derive from
  `bkd_base_url`).
- **Webhook**: when initialising a fresh REQ, persist
  `ctx.bkd_intent_url = bkd_issue_url(project_id, intent_issue_id)` alongside
  the existing `intent_issue_id` / `intent_title`.
- **`create_pr_ci_watch.create_pr_ci_watch`**: before either the checker or
  BKD-agent dispatch path, run a lightweight `gh api /repos/:owner/:repo/pulls
  ?head=:owner:feat/REQ-...` per involved repo to capture
  `{repo: html_url}`. Persist to `ctx.pr_urls`. Failures degrade silently
  (warning + leave `pr_urls` unset); the action continues to the existing
  dispatch.
- **`gh_incident.open_incident`**: accept `bkd_intent_url: str | None` and
  `pr_urls: dict[str, str] | None` kwargs. Body gains two new lines:
  - `**BKD intent issue**: [<id>](<bkd_intent_url>)` (existing line keeps the
    raw id; URL is appended in markdown link form when available).
  - `**PRs**: [foo/bar#9](https://github.com/foo/bar/pull/9), …` (new line,
    only when `pr_urls` non-empty).
- **`escalate.escalate`**: pull `bkd_intent_url` and `pr_urls` from `ctx` and
  pass through to every `open_incident` call.
- **`start_analyze`**: pass `bkd_intent_issue_url` template var to
  `analyze.md.j2`.
- **`analyze.md.j2`**: append a "PR body footer" section instructing the
  agent to include a fixed sisyphus-cross-link block verbatim in every PR
  body it opens, with REQ id, BKD issue URL, and a `<!-- sisyphus:cross-link
  ... -->` HTML comment for downstream tooling to detect.
- **`done_archive` action + `done_archive.md.j2`**: pass `pr_urls` from ctx
  to the prompt so the archive agent has the URL list pre-rendered as
  markdown bullets (avoids re-running `gh pr list`).
- **Metabase `05-active-req-overview.sql`**: add two columns
  - `bkd_intent_url` selected from `r.context->>'bkd_intent_url'`
  - `pr_urls_md` computed via `jsonb_object_agg → string_agg(format(...))`
    so Metabase can render markdown in a "Markdown" column.

## Out of scope

- GitHub PR webhook listener (no bidirectional event correlation — too much
  ops surface for this REQ's value).
- Adding pr_urls capture inside the BKD-agent fallback path of pr-ci-watch
  (the action-level `discover_pr_urls` covers both paths).
- Renaming or deleting existing `intent_issue_id` raw-id fields. The new URL
  fields are additive; legacy consumers keep working.

## Risk / mitigation

- **Stale `bkd_frontend_url` derivation** if a deployment uses a non-`/api`
  base URL pattern. Mitigation: explicit override; default helper returns
  `None` when both `bkd_base_url` is malformed and override is empty (no
  broken-link injection).
- **Extra GH REST call in `create_pr_ci_watch`** before the checker /
  BKD-agent path. Cost: 1 GET per involved repo (typically 1-2). Failure
  degrades silently — never blocks pr-ci-watch dispatch.
- **`gh_incident.open_incident` signature widening**. New kwargs are
  optional and keyword-only; existing call sites and contract tests
  (`test_contract_gh_incident_open.py` GHI-S1..S15) keep passing because
  they inspect `call.get(name)` rather than full kwargs equality.
