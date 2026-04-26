# Tasks — REQ-one-pr-per-req-1777218057

## Stage: contract / spec
- [x] author `specs/gh-incident-open/spec.md` delta (MODIFIED Requirements + ADDED comment-on-pr Requirement) — `openspec/changes/REQ-one-pr-per-req-1777218057/specs/gh-incident-open/spec.md`
- [x] author `proposal.md` (motivation + impact summary)
- [x] author `design.md` (decision tree, ctx schema, failure modes)

## Stage: implementation
- [x] add `gh_incident.find_pr_for_branch(repo, branch) -> int | None` (httpx GET `/repos/{repo}/pulls?head=...&state=all`)
- [x] add `gh_incident.comment_on_pr(*, repo, pr_number, ...) -> str | None` (httpx POST `/repos/{repo}/issues/{pr_number}/comments`, returns `html_url`)
- [x] update `actions/escalate.py` per-repo loop: try PR comment first, fall through to `open_incident` when no PR
- [x] persist `ctx.gh_incident_kinds: dict[str, "comment" | "issue"]` alongside existing `ctx.gh_incident_urls`
- [x] preserve existing per-repo idempotency on `ctx.gh_incident_urls`
- [x] preserve existing `github-incident` BKD tag semantics
- [x] unit tests: `find_pr_for_branch` happy path / 404 / network error / token-empty short-circuit
- [x] unit tests: `comment_on_pr` happy path / 4xx / network error / token-empty short-circuit
- [x] integration tests in `test_gh_incident.py`: escalate posts comment when PR exists; falls back to issue when PR missing; multi-repo mixed (one comment, one issue); per-repo idempotency

## Stage: PR
- [x] `git push origin feat/REQ-one-pr-per-req-1777218057`
- [x] `gh pr create` (one PR on phona/sisyphus, the only involved repo)
