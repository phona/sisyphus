# REQ-one-pr-per-req-1777218057 — feat: 1 REQ = 1 PR (incident comment-on-PR)

## Why

Today every REQ that escalates leaves behind two GitHub artifacts per
involved-repo:

1. The `feat/{REQ}` PR (opened by the analyze-agent), and
2. A separate **incident issue** opened by `gh_incident.open_incident()`
   (`https://api.github.com/repos/{repo}/issues`).

The invariant "1 REQ = 1 PR" — single point of truth on GitHub — is broken: the
human reviewer triaging an escalation has to context-switch between a PR (where
the diff lives) and a parallel incident issue (where the failure metadata
lives). With per-involved-repo escalation (REQ-gh-incident-per-involved-repo),
multi-repo REQs spawn N issues + N PRs, multiplying the noise.

## What changes

The `escalate` action posts the incident metadata as a **PR comment** on the
existing `feat/{REQ}` PR for each involved-repo, instead of creating a fresh
GitHub issue.

The legacy issue-creation path is preserved as a **fallback**: when there is no
PR for `feat/{REQ}` on a given repo (escalations during INTAKING / early
ANALYZING that fire before the analyze-agent has pushed any branch, or
deployments that point `gh_incident_repo` at a triage inbox repo with no per-REQ
PR), the action falls through to `gh_incident.open_incident()` exactly as
today. This keeps "human always sees something" as the strict guarantee while
eliminating the duplicate artifact in the common steady-state path.

`ctx.gh_incident_urls` continues to be the persisted record of what was opened
on GitHub. URLs may now point to either an issue-comment URL
(`/repos/{repo}/issues/{pr_number}#issuecomment-{id}`) or a fresh-issue
`html_url`; ctx.gh_incident_kinds (new field) records `"comment" | "issue"` per
repo so the admin view / dashboards can distinguish the two paths.

## Impact

- **`orchestrator/src/orchestrator/gh_incident.py`** — adds `find_pr_for_branch`
  + `comment_on_pr`; keeps `open_incident` unchanged for the fallback path.
- **`orchestrator/src/orchestrator/actions/escalate.py`** — for each
  incident-target repo, looks up the `feat/{REQ}` PR via REST; when found,
  posts a comment; when not found, falls through to `open_incident` as today.
- **`ctx`** — adds `gh_incident_kinds: dict[str, "comment" | "issue"]`
  alongside the existing `gh_incident_urls` / `gh_incident_url` /
  `gh_incident_opened_at`. The BKD tag `github-incident` is appended in both
  the comment and the issue path (semantics unchanged: "humans should look at
  GitHub for this REQ").
- **Settings** — no new knobs; the existing `github_token` PAT already needs
  Issues:Write (which covers PR comments — they're issues under the hood).
- **Backwards compat** — no migrations; `gh_incident_url` /
  `gh_incident_urls` field shapes are unchanged. Existing escalated REQs
  with an `gh_incident_url` field set are still readable.
