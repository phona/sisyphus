# escalate-pr-merged-override

## ADDED Requirements

### Requirement: escalate action MUST short-circuit to DONE when all involved-repo PRs are merged

The `actions.escalate` handler MUST, before performing any auto-resume
follow-up, GH incident issue creation, BKD `escalated` tag write, or
terminal CAS to `ESCALATED`, query the GitHub REST API for the merge
state of `feat/{req_id}` PRs across the REQ's involved repositories
(resolved via `_clone.resolve_repos` layers 1-4: intake-finalized-intent /
ctx.involved_repos / `repo:<org>/<name>` BKD tags / `settings.default_involved_repos`).
The handler SHALL treat the REQ as completed (state DONE) when at least
one resolved repo has an open-or-closed PR for that branch AND every
found PR has a non-null `merged_at` timestamp. On override, the handler
MUST CAS the REQ row to `state='done'` via `req_state.cas_transition`
with `event=Event.ARCHIVE_DONE` and `action='escalate_pr_merged_override'`,
patch context with `completed_via='pr-merge'`, and skip the regular
escalate side-effects. The override MUST also schedule a fire-and-forget
`cleanup_runner(retain_pvc=False)` so PVC is released immediately rather
than retained for human debug (mirroring the `admin/complete` semantics).

#### Scenario: PMO-S1 single repo with merged PR overrides escalate to DONE

- **GIVEN** a REQ in any non-DONE state (e.g. `accept-running`) with
  `ctx.involved_repos=["phona/sisyphus"]`
- **AND** GitHub returns one PR for `feat/REQ-X` head whose `merged_at`
  is not null
- **WHEN** `escalate(body, req_id, tags, ctx)` is invoked from
  `engine.step` (any path: VERIFY_ESCALATE, SESSION_FAILED, INTAKE_FAIL, etc.)
- **THEN** `req_state.cas_transition` MUST be called once with
  `next_state=ReqState.DONE`, `event=Event.ARCHIVE_DONE`, and
  `action='escalate_pr_merged_override'`
- **AND** the BKD intent issue MUST receive `tags add` containing
  `done` and `via:pr-merge`
- **AND** the BKD intent issue MUST NOT receive an `escalated` tag,
  any `reason:*` tag, or a `github-incident` tag
- **AND** `gh_incident.open_incident` MUST NOT be invoked
- **AND** the action return value MUST be a dict with
  `escalated == False` and `completed_via == 'pr-merge'`

### Requirement: escalate action MUST proceed with the original escalate flow when not all PRs are merged

`actions.escalate` MUST continue with its existing behavior whenever the
merge check returns False — including when no involved repos resolved,
GitHub API failed, no PR exists for any repo, or any found PR is not
merged. The fall-through path MUST run auto-resume on transient failures
with `auto_retry_count < 2`, otherwise GH incident creation, `escalated`
+ `reason:<...>` BKD tags, ctx writes for `escalated_reason` /
`gh_incident_urls`, and final CAS to `ESCALATED` (for SESSION_FAILED
self-loop paths). The merge-check probe itself MUST NOT raise exceptions
out of `escalate` — any HTTPX / ValueError MUST be caught locally and
treated as "cannot determine merge state → return False → fall through".

#### Scenario: PMO-S2 single repo with open PR falls through to original escalate

- **GIVEN** a REQ with `ctx.involved_repos=["phona/sisyphus"]` and the
  GH response for `head=phona:feat/REQ-X&state=all` returns one PR with
  `merged_at=null`
- **WHEN** `escalate` is invoked with body event `session.completed` and
  `ctx.escalated_reason='verifier-decision-escalate'`
- **THEN** the action MUST NOT CAS to DONE
- **AND** `bkd.merge_tags_and_update` MUST be called with `add` including
  `escalated`
- **AND** `gh_incident.open_incident` MUST be invoked exactly once
  (single involved repo)
- **AND** the action return value MUST contain `escalated == True`

### Requirement: escalate MUST require at least one merged PR before overriding to DONE

The merge check MUST NOT return True trivially on empty input. If
`resolve_repos` returns an empty list, or if every queried repo returns
zero PRs for `feat/{req_id}`, the helper MUST return False so that
intake-stage failures (pre-clone, pre-PR) and early-stage escalate paths
behave identically to their pre-fix flow.

#### Scenario: PMO-S3 multi-repo all merged overrides to DONE

- **GIVEN** a REQ with `ctx.involved_repos=["phona/repo-a","phona/repo-b"]`
- **AND** GH returns one merged PR for each repo's `feat/REQ-X` branch
- **WHEN** `escalate` is invoked
- **THEN** `req_state.cas_transition` MUST be called with
  `next_state=ReqState.DONE`
- **AND** the action result MUST contain `completed_via='pr-merge'` and
  `completed_repos` listing both repos

#### Scenario: PMO-S4 multi-repo with one open PR falls through

- **GIVEN** a REQ with `ctx.involved_repos=["phona/repo-a","phona/repo-b"]`
- **AND** GH returns a merged PR for repo-a but an open (non-merged) PR
  for repo-b
- **WHEN** `escalate` is invoked
- **THEN** the action MUST NOT CAS to DONE
- **AND** the existing escalate flow MUST run (BKD `escalated` tag added)

#### Scenario: PMO-S5 no involved_repos resolved falls through

- **GIVEN** a REQ with empty ctx (no `involved_repos`, no `intake_finalized_intent`,
  no `repo:` tags) and `settings.default_involved_repos=[]`
- **WHEN** `escalate` is invoked
- **THEN** the merge-check helper MUST return False without making any
  GH HTTP request
- **AND** the existing escalate flow MUST run

### Requirement: escalate MUST tolerate GitHub API failures during merge probe

The `_all_prs_merged_for_req` helper MUST return False whenever any
GitHub REST call inside it raises `httpx.HTTPError` or `ValueError` —
the caller MUST then fall through to the original escalate flow. The
exception MUST be logged at WARNING level with `repo` and `error`
fields, and MUST NOT propagate out of `escalate`.

#### Scenario: PMO-S6 GH 503 during merge probe falls through to escalate

- **GIVEN** a REQ with `ctx.involved_repos=["phona/sisyphus"]`
- **AND** GH `/pulls` returns HTTP 503
- **WHEN** `escalate` is invoked
- **THEN** the helper MUST log a warning and return False
- **AND** the existing escalate flow MUST run
- **AND** no exception MUST propagate from `escalate`

### Requirement: escalate override path MUST clean up runner resources non-retainable

When the override path fires, `escalate` MUST schedule a fire-and-forget
`k8s_runner.get_controller().cleanup_runner(req_id, retain_pvc=False)`.
The `retain_pvc=False` value mirrors `admin/complete`'s semantics: a DONE
REQ is not a debug target, the PVC MUST be released to free disk
immediately. This is distinct from the regular escalate path which uses
`retain_pvc=True` so a human can spelunk the workspace post-mortem.

#### Scenario: PMO-S7 override path triggers cleanup with retain_pvc=False

- **GIVEN** a REQ with `ctx.involved_repos=["phona/sisyphus"]` whose PR
  is merged AND a runner pod exists for the REQ
- **WHEN** `escalate` is invoked
- **THEN** `k8s_runner.RunnerController.cleanup_runner` MUST be called
  with `retain_pvc=False` (whether via direct call or fire-and-forget
  asyncio.Task)
- **AND** the cleanup MUST be scheduled (not awaited synchronously) so
  the action returns promptly

### Requirement: escalate override BKD tag merge MUST add done and via:pr-merge but not escalated

The BKD intent issue tag PATCH on the override path MUST add the literal
strings `done` and `via:pr-merge` to the issue's tag set (using
`bkd.merge_tags_and_update` so existing tags are preserved). The PATCH
MUST NOT add `escalated`, any `reason:*` tag, or `github-incident`. The
issue's `statusId` SHOULD also be updated to `done` so the BKD UI
reflects the terminal state.

#### Scenario: PMO-S8 override path BKD tag set has done + via:pr-merge

- **GIVEN** a REQ whose PR is merged
- **WHEN** `escalate` is invoked and the override path fires
- **THEN** `bkd.merge_tags_and_update` MUST be called with `add` set
  containing both `done` and `via:pr-merge`
- **AND** the same `add` set MUST NOT contain `escalated`
- **AND** the same `add` set MUST NOT contain any string starting with
  `reason:`
- **AND** the same `add` set MUST NOT contain `github-incident`
