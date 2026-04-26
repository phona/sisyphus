# Design: per-involved-repo gh-incident

## Resolution layer order

The escalate action already knows two ways to find "what repos this REQ touches":

1. The clone helper's `resolve_repos(ctx, tags=, default_repos=)` walks
   `intake_finalized_intent.involved_repos` → `ctx.involved_repos` → `tags repo:` →
   `settings.default_involved_repos` and returns the first non-empty list. This is the
   exact information we want for incident routing — if you cloned repo-a + repo-b for
   the dev stage, an escalation in those stages should surface in repo-a + repo-b.
2. `settings.gh_incident_repo` is the existing single-inbox knob.

Reusing `resolve_repos` keeps "where the code lives" and "where the incident lands"
consistent — there's no scenario where we cloned repo-a but the incident lands somewhere
else. The single-inbox knob is repurposed as **layer 5**: only consulted when layers 1-4
are all empty (intake-stage escalate, infra failure pre-clone, and so on). When layers
1-4 yield repos, the single-inbox knob is **ignored**; this avoids "every multi-repo REQ
posts N+1 issues" (one extra in the central inbox), which would just dilute the central
inbox into noise.

## ctx shape

- **New**: `ctx.gh_incident_urls: dict[str, str]` — `{repo_slug: html_url}` for every
  successfully opened incident. This is the source of truth for idempotency: if
  `repo-a` is already a key, skip the POST to `repo-a` on re-entry. Repo lookups are
  exact-string (slug from layer N matches slug used in the prior call).
- **Kept for compat**: `ctx.gh_incident_url: str` — set to the first newly opened URL
  in this call (or, if all repos were already-handled idempotently, left untouched).
  This keeps the single-URL admin view (`curl /admin/req/{id}`) and downstream
  Metabase queries unchanged for single-repo deployments. New code reads
  `gh_incident_urls`; legacy readers keep working off `gh_incident_url`.
- **Unchanged**: `ctx.gh_incident_opened_at` is set on the first successful POST in
  the call (one timestamp per escalate invocation, not per repo).

## Per-repo failure isolation

The original implementation wrapped `open_incident` in a try/except inside the function
itself; we keep that. The new escalate loop additionally treats each repo's POST
independently — a 4xx/5xx on `repo-a` returns `None` from `open_incident`, the loop
skips that repo, and the next repo proceeds. Operationally this matters when:

- The orchestrator PAT has Issues:Write on `repo-a` but not `repo-b` (cross-org REQ);
  `repo-a` gets an incident, `repo-b` is logged and skipped.
- GitHub returns 5xx mid-loop; we don't retry inside escalate (that's the caller's
  retry boundary), but partial success is still useful.

The `github-incident` BKD tag is added if **any** repo got a URL (either freshly
opened or pre-existing in `gh_incident_urls`).

## Why we don't migrate `gh_incident_url` → `gh_incident_urls` automatically

Old contexts (REQs that escalated under the v1 implementation) have only
`gh_incident_url` set. We deliberately do **not** auto-fill `gh_incident_urls` from
`gh_incident_url` because:

- The legacy URL has no recorded `repo` — we'd be guessing. Wrong-key fills would let
  the per-repo idempotency path fire a duplicate POST against a different repo than
  the one originally targeted.
- Old REQs that escalated under v1 are already terminal; the worst-case re-entry
  (admin resume → escalate again) is rare and the duplicate cost (one extra issue
  in the legacy single-inbox repo) is acceptable.

So: read `gh_incident_urls` for the per-repo idempotency check; ignore
`gh_incident_url` for that purpose; new escalates always write `gh_incident_urls`.

## Tradeoffs left for later

- **Per-repo `gh_incident_opened_at`**: we keep one global timestamp per escalate
  invocation rather than per repo, since downstream Metabase queries already group
  by REQ; per-repo timestamps would just add noise without a known consumer.
- **Cross-issue link-back**: we don't post a "see also: phona/repo-a#42" comment
  on each repo's incident. Operators get the cross-reference via the BKD intent
  issue's `github-incident` tag → `ctx.gh_incident_urls` → all URLs. Adding
  comment edges later is a pure addition.
- **Incident close on resume**: still out of scope (matches v1 design), since
  closing requires a separate signal point in `done_archive`/admin-resume.
