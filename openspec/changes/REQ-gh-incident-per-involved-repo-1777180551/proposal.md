# REQ-gh-incident-per-involved-repo-1777180551: open one GH incident per involved source repo

## Why

Today `escalate.py` opens **one** GH incident issue in `settings.gh_incident_repo` (a single
configured central inbox, e.g. `phona/sisyphus`). For multi-repo REQs that touch more than
one source repo (e.g. `phona/repo-a` + `phona/repo-b`), the failure surfaces only in the
central inbox — the maintainers of the actual code repos never see the incident in their
own backlog. Symmetrically, when `gh_incident_repo` is left empty (current safe default),
no issue is opened anywhere even though the involved repos are already known and the PAT
has access.

The fix: when the REQ's involved repos are resolvable (intake intent / ctx /
`repo:` tags / `default_involved_repos`), open **one incident per involved repo** so each
maintainer sees the failure in their own queue. The legacy single-inbox config stays as a
fallback for REQs whose involved repos are unknown (intake-stage failures, unconfigured
fallback) and as an explicit "send everything to a central triage queue" knob.

## What changes

- **MOD** `orchestrator/src/orchestrator/gh_incident.py`:
  `open_incident(...)` now takes `repo: str` as an explicit kwarg. The function MUST
  POST to `/repos/{repo}/issues` and MUST return `None` (without HTTP) when `repo` is
  empty or `settings.github_token` is empty. The disabled-when-empty-token semantic
  stays; the disabled-when-empty-repo semantic moves from "global setting" to "per-call
  argument empty."
- **MOD** `orchestrator/src/orchestrator/actions/escalate.py`:
  In the real-escalate branch, resolve the **list of incident-target repos** via the
  same multi-layer fallback that drives server-side cloning
  (`actions/_clone.resolve_repos`):
  1. `ctx.intake_finalized_intent.involved_repos`
  2. `ctx.involved_repos`
  3. tags `repo:<org>/<name>`
  4. `settings.default_involved_repos`
  5. `settings.gh_incident_repo` (last-resort single-inbox fallback)
  Iterate, and for each repo not already in `ctx.gh_incident_urls`, call
  `open_incident(repo=...)`. Persist the per-repo URLs to
  `ctx.gh_incident_urls: dict[str, str]` (repo → url). Keep `ctx.gh_incident_url`
  populated to the first successful URL for backward compatibility with admin views.
  Per-repo failures are isolated: a 4xx/5xx on `repo-a` MUST NOT prevent the POST to
  `repo-b`, and at least one successful URL is enough to add the `github-incident`
  BKD tag.
- **MOD** `orchestrator/tests/test_gh_incident.py`: refresh GHI-S1..S10 to use the new
  `repo=` kwarg; add per-involved-repo scenarios.
- **MOD** `orchestrator/tests/test_contract_gh_incident_open.py`: same refresh.
- **NEW** specs/gh-incident-open delta: MODIFIED open_incident requirement (signature
  change: `repo` kwarg) + MODIFIED escalate-action requirement (per-repo loop +
  fallback chain) + ADDED Requirement: "incident repo resolution layers."

## Impact

- **Affected code**: `orchestrator/src/orchestrator/{gh_incident.py,actions/escalate.py}`,
  the two unit/contract test files, `openspec/specs/gh-incident-open/spec.md`.
- **Affected ops**: when `involved_repos` is non-empty (multi-repo REQ in intake or with
  `repo:` tags), each REQ now creates **N** issues — one per involved repo — instead of
  one. The PAT (`SISYPHUS_GITHUB_TOKEN`) MUST have `Issues: Read-and-write` on every
  involved repo; if a repo lacks write scope the per-repo POST fails with 403, gets
  logged at warning, and the rest of the loop continues. The `gh_incident_repo`
  knob is unchanged in shape but is **demoted** from "primary target" to "last-resort
  fallback when involved_repos is empty"; setups that today rely on it (single-inbox
  triage) keep working with no helm change.
- **Risk**: low — the per-repo loop short-circuits on empty involved_repos to the
  existing single-inbox path; idempotency now keys on `(repo, gh_incident_urls)` so
  resume cycles still produce one issue per repo.
- **Rollout**: no helm change required. Operators running multi-repo REQs will see
  N incidents per real-escalate; if that volume is undesired, set `gh_incident_repo`
  + leave `default_involved_repos` empty + don't tag intent issues with `repo:`,
  and you fall through to the legacy single-inbox path automatically.
