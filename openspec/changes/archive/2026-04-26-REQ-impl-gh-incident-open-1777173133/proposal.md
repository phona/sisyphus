# REQ-impl-gh-incident-open-1777173133: Open a GitHub incident issue on REQ escalate

## Why

When a REQ enters `ESCALATED` (terminal failure that needs a human), today the only signal is:
- A `escalated` + `reason:<reason>` tag on the BKD intent issue.
- A row in `req_state` with `context.escalated_reason`.
- A log line.

The operations team triages incidents in **GitHub issues** — `gh issue list --label sisyphus:incident`
is already the documented surface (see `orchestrator/docs/sisyphus-integration.md` §10) and
`snapshot.py` already reserves `github-incident` as a known stage tag. Today no code actually
creates that issue, so escalations sit in BKD UI and are easy to miss until the next standup.

Opening a GitHub issue automatically when sisyphus declares `ESCALATED` closes that gap:
- Surfaces the failure in the canonical SRE workflow (assign / label / milestone / link to PR).
- Provides a stable URL to attach to postmortems and Metabase Q-decisions.
- Cross-references the BKD intent issue (full agent session log) for deep dive.

## What changes

- **NEW** `orchestrator/src/orchestrator/gh_incident.py`:
  `open_incident(...)` POSTs `/repos/{owner}/{repo}/issues` with a structured body
  containing REQ id, reason, retry count, BKD intent issue link, failed sub-issue id, and
  state-at-escalate. Returns the new issue's `html_url`, or `None` on disabled / failure.
  GH-side failures **never block** the escalate flow.
- **MOD** `orchestrator/src/orchestrator/actions/escalate.py`:
  In the "real escalate" branch (after BKD `merge_tags_and_update`, before final log),
  call `open_incident(...)` and extend `update_context` with `gh_incident_url` +
  `gh_incident_opened_at`. Append `github-incident` to the BKD tag merge so existing
  snapshot/Metabase pipelines pick it up.
- **MOD** `orchestrator/src/orchestrator/config.py`:
  Add `gh_incident_repo: str = ""` (empty = disabled, default safe) and
  `gh_incident_labels: list[str] = ["sisyphus:incident"]`. Reuse existing
  `settings.github_token` (must have `Issues: Read-and-write`).
- **MOD** `orchestrator/helm/values.yaml` + `helm/templates/configmap.yaml`:
  Surface the two new knobs and document the required PAT scope upgrade.
- **Idempotency**: skip POST if `ctx.gh_incident_url` already exists. Auto-resume cycles
  may run `escalate` again later; we never want a second issue per REQ.
- **No new state / event**. The state machine still goes
  `(any running) → ESCALATED` exactly as today.

## Impact

- **Affected code**: `orchestrator/src/orchestrator/{gh_incident.py,actions/escalate.py,config.py}`,
  `orchestrator/helm/{values.yaml,templates/configmap.yaml}`.
- **Affected ops surface**: a new GitHub issue per real escalation in the configured repo
  (default `""` = disabled, so dev / unconfigured installs see no behavior change).
- **PAT scope**: when `gh_incident_repo` is set, `SISYPHUS_GITHUB_TOKEN` MUST have
  `Issues: Read-and-write` for that repo. Today the PAT only needs read for `pr_ci_watch`;
  enabling this feature is an opt-in upgrade.
- **Risk**: small — guarded by config (default disabled), wrapped in try/except (GH outage
  doesn't break escalate), and idempotent (resume cycles don't dup).
- **Rollout**: ship code with `gh_incident_repo=""` (no-op). Operator sets
  `env.gh_incident_repo: phona/sisyphus` in helm values when they're ready to receive.
