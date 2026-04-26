# Design: gh-incident-open on escalate

## Where the call belongs

`escalate.py` already has two distinct branches:

1. **Auto-resume** — transient signal + retry budget left. Sends a BKD follow-up,
   bumps `auto_retry_count`, returns `{"auto_resumed": True}`. **State does not change.**
   No GH issue here — would be noise.
2. **Real escalate** — retry budget gone, hard reason, or non-transient signal. Merges
   `escalated` + `reason:<reason>` tag onto the BKD intent issue, writes
   `escalated_reason` into ctx, optionally CAS-pushes state to `ESCALATED` and cleans
   the runner. **This is where a GH incident belongs.**

We hook in **between** the BKD tag merge and the optional CAS/cleanup: the BKD tag
write is the conceptual "REQ has failed for real" moment, and we want the GH issue
URL to be available for the same `update_context` call so it lands atomically with
`escalated_reason`.

## Idempotency

Two distinct re-entry paths exist:

- **Same escalate call retries** — handler is `idempotent=True` (engine may replay it).
- **Resume → re-escalate cycle** — admin `/req/{id}/resume` puts the REQ back to a
  running state; if it fails again, `escalate` runs again with the same `intent_issue_id`.

In both cases we read `ctx.gh_incident_url` first; if present we skip the POST and
return the existing URL. This trades a single round-trip read for guaranteed
"one issue per REQ", which matches how humans expect incident tickets to behave.

## Failure mode

`open_incident` is wrapped in a single try/except. Any exception (HTTP timeout,
401/403, repo not found, GH 5xx) → log warning, return `None`. The escalate
action proceeds: BKD tag is set, state goes ESCALATED, runner is cleaned. The
operator notices the missing GH issue via the absence of `ctx.gh_incident_url`
in the admin REQ view — fine for v1, can add a watchdog later.

We deliberately do **not** retry on GH failure inside `escalate`. The escalate
path is itself the retry boundary; layering a second retry inside risks
re-entering during state transitions and bloats the action's responsibility.
A separate watchdog can backfill missed incidents if needed (out of scope here).

## Issue body shape

Plain-text Markdown, designed for human triage. Includes:

- Header line: `**REQ**: {req_id}` and `**Reason**: {final_reason}`.
- Counters: `retry_count`, BKD project id.
- Cross-links: BKD intent issue id (operator can deep-link in BKD UI), failed
  sub-issue id (`escalated_source_issue_id`), state at escalate.
- Timestamp (UTC ISO).
- A short "What to do" stub pointing at the runbook (admin resume endpoint).

Labels: configurable base list (`["sisyphus:incident"]` default) plus a
per-incident `reason:{reason}` label so the existing label-search workflow
keeps working.

## Why reuse `settings.github_token`

The orchestrator already holds a GitHub PAT for `pr_ci_watch` (read-only check-runs
GET). For incident-open we need `Issues: Read-and-write` on the incident repo.
Two paths considered:

- **Reuse `github_token`**: one PAT, scope upgraded. Documented in helm values.
  Fewer secrets to rotate. Picked.
- **Add `gh_incident_token`**: separate write-scope PAT, principle of least authority.
  More config churn, two secrets to maintain. Rejected for v1 — can split later
  if the PAT is shared across orgs and a per-org write scope is needed.

The runner Pod's `gh_token` (separate, fine-grained read-only PAT) is **not**
touched; it stays read-only as the "runner = read-only checker" contract
demands (`docs/architecture.md` §8).

## Why no new state machine work

Opening the GH issue is a side-effect of going ESCALATED, not a new lifecycle
stage. Adding an `INCIDENT_OPENING` state would buy nothing — there's nothing
downstream that waits on the GH issue, and a half-opened incident on a crash
is recoverable by re-running escalate (which is idempotent on `gh_incident_url`).

## Tradeoffs left for later

- We don't link the GH issue back to itself in BKD as a clickable URL tag — BKD
  tags are searchable strings, not links. Operators get the URL via
  `curl /admin/req/{id}` (`ctx.gh_incident_url`) or by clicking the
  `github-incident` label in BKD then cross-referencing.
- We don't close the GH issue when the REQ is resumed and eventually completes.
  Closing requires a separate signal point in `done_archive`; the operator can
  close manually for now and we'll automate when the workflow demands it.
