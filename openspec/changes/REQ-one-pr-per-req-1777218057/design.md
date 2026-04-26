# Design: incident comment-on-PR

## Decision: comment first, fall back to fresh issue

For each `(repo, feat/{REQ})` pair the escalate action handles per the existing
layered involved-repos resolver, the new flow is:

```
for repo in incident_repos:
    if repo in ctx.gh_incident_urls: continue          # idempotent
    pr_number = await gh_incident.find_pr_for_branch(repo, f"feat/{req_id}")
    if pr_number is not None:
        url = await gh_incident.comment_on_pr(repo, pr_number, ...)
        kind = "comment"
    else:
        url = await gh_incident.open_incident(repo, ...)  # legacy
        kind = "issue"
    if url:
        new_urls[repo] = url
        new_kinds[repo] = kind
```

Why fall back rather than skip when no PR exists: escalations can fire from
INTAKING / early ANALYZING (intake-fail, analyze action-error before any push)
where there is no `feat/{REQ}` branch on GitHub yet. In those states the human
still needs a triage surface; an opened issue is the only one available.

Why not always do both: the PR comment alone covers the "1 REQ = 1 PR"
invariant; doubling up just re-introduces the noise the change is meant to
eliminate.

## API: GitHub PR comments

PR comments are issue-level comments (PR is an issue subtype). The endpoint is
`POST /repos/{owner}/{repo}/issues/{pr_number}/comments` with body
`{"body": "..."}`. Required PAT scope: `repo` / `issues: write`. The same PAT
already used by `open_incident` is sufficient — no new permission ask. Response
`html_url` is the comment permalink; that's what we persist to
`ctx.gh_incident_urls[repo]`.

## API: PR lookup

`GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=all&per_page=5`.
We accept any state (open / closed / merged) — escalations can land after a PR
is merged (e.g., archive crash) and the comment is still useful audit. We take
the first PR returned (GitHub orders by newest).

If the API errors (network, 5xx, 403 from PAT scope mismatch), we treat the
result as "PR not found" and fall through to `open_incident` rather than
failing the escalate path. The escalate action's contract — "GitHub failures
never block the state transition" — is preserved.

## Idempotency model

Unchanged from existing behavior: per-repo idempotency on `ctx.gh_incident_urls`.
If the same `(repo, REQ)` pair already has a URL recorded, skip everything
(both PR lookup and comment) regardless of whether the prior URL was a comment
or an issue. This means a REQ that escalates → admin-resumes → re-escalates
posts at most one GitHub artifact per repo for its lifetime, matching today.
A future REQ can extend to per-(repo, escalation-round) if reviewers want a
timeline; out of scope here.

## ctx schema additions

| field | shape | notes |
|---|---|---|
| `gh_incident_urls` | `dict[str, str]` | unchanged; URL is comment permalink **or** issue url |
| `gh_incident_kinds` | `dict[str, "comment"\|"issue"]` | **new**; per-repo, set to whichever path landed |
| `gh_incident_url` | `str` | unchanged legacy single-URL field; first newly minted URL of the call |
| `gh_incident_opened_at` | ISO8601 string | unchanged |

`gh_incident_kinds` is purely additive — admin and dashboard read `context`
opaquely so no consumers need updates. Optional for downstream tools to use.

## Failure modes and what's preserved

- GitHub PR-lookup network error → fall through to `open_incident` (issue
  fallback). Same behavior as today (no PR found = always issue).
- Comment POST 4xx / 5xx → log warning, return None, escalate continues; no
  retry inside the escalate boundary (matches `open_incident` policy).
- All-empty involved repos + no `gh_incident_repo` → skip entirely (no
  comment, no issue, no `github-incident` tag). Unchanged from today.
- `github_token` empty → both `find_pr_for_branch` and `comment_on_pr` short-
  circuit (return None); fall through gives empty result; escalate proceeds
  with no GitHub artifact and no `github-incident` tag. Matches today.

## What this does NOT change

- `open_incident()` signature, body shape, headers, label list — untouched.
- BKD tags (`escalated`, `reason:*`, `github-incident`) — semantics unchanged.
- Auto-resume path — still no GitHub interaction.
- PR-merged shortcut — runs before this code path; unaffected.
- Per-involved-repo loop, layered fallback resolver — both reused as-is.
