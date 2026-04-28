# fix(snapshot): trigger INTENT_ANALYZE for missing req

## Why

The orchestrator's `intent:analyze` entry path is webhook-only. When a user
adds the `intent:analyze` tag to a fresh BKD issue, BKD posts
`issue.updated` to `/bkd-events`, the webhook resolves `req_id` (from a
`REQ-*` tag or — for fresh intent — falls back to `REQ-{issueNumber}`),
runs `req_state.insert_init`, and calls `engine.step` with
`(INIT, INTENT_ANALYZE) → ANALYZING + start_analyze`.

If that single webhook is missed (sisyphus restart between BKD's POST and
our handler, BKD network blip, transient 5xx that BKD never retries),
the REQ is stranded:

- BKD issue still carries `intent:analyze`,
- no `req_state` row,
- no `bkd_snapshot` row either (current sync only writes when the issue
  already has a tracked REQ),
- the only recovery is a human noticing and toggling the tag — which
  re-fires the webhook only if BKD's tag-removal-then-readd path re-emits
  the event.

We already have a 5-minute sync_once loop that polls every project's BKD
issue list. That loop is the natural place to detect this orphan and
trigger the missed `INTENT_ANALYZE` event itself.

## What Changes

- **Snapshot loop adds an orphan-recovery pass.** While iterating BKD
  issues per project, `sync_once` now also runs
  `_trigger_orphan_intent_analyze`: for each issue that
  - has `intent:analyze` in its tags,
  - does **not** have `analyze` (i.e. has not yet entered the analyzing
    state via the normal entry action),
  - is not in BKD status `done`/`cancelled` (user-terminated),
  - resolves to a `req_id` (REQ-* tag if present, else
    `REQ-{issueNumber}` like the webhook fallback),
  - and has no row in `req_state`,
  the loop synthesizes a webhook-shaped `body` and runs the same
  `req_state.insert_init` + `engine.step(INTENT_ANALYZE)` sequence the
  webhook handler would have.

- **No new state machine transitions, no new actions.** Recovery uses
  the existing `(INIT, INTENT_ANALYZE) → ANALYZING + start_analyze`
  path. CAS in `cas_transition` and the `ON CONFLICT DO NOTHING` guard
  in `insert_init` make the trigger safe to race against a delayed
  webhook arrival.

- **Snapshot startup gate decoupled from observability DB.** Today
  `main.py` only starts `snapshot.run_loop` when both
  `snapshot_interval_sec > 0` **and** `obs_pg_dsn` is set, because the
  loop's only job was the `bkd_snapshot` UPSERT. Orphan recovery has
  nothing to do with the obs DB, so the gate becomes
  `snapshot_interval_sec > 0` alone. `sync_once` keeps the obs UPSERT
  best-effort: it skips the UPSERT when `obs_pool is None` while still
  performing orphan recovery.

- **`sync_once` keeps its `int` return type.** The
  orch-noise-cleanup contract `ORCHN-S3` already pins the return shape
  ("returns 0 when every project_id is excluded"), so we keep
  `sync_once: () -> int` returning the `bkd_snapshot` UPSERT count. The
  number of orphan triggers is reported on the `snapshot.synced` log
  line as `orphans_triggered=<n>`; tests assert on `engine.step` /
  `req_state.insert_init` mocks rather than the return value.

- **Tests** (additions to `orchestrator/tests/test_snapshot.py`):
  - existing `test_sync_once_*` cases updated to read the dict return.
  - new `test_orphan_intent_analyze_triggers_when_missing_req_state` —
    BKD issue with `intent:analyze` tag, no REQ tag, no `req_state`
    row → `engine.step` is called with `cur_state=INIT` and
    `event=INTENT_ANALYZE`, and `req_state.insert_init` runs first.
  - new `test_orphan_intent_analyze_skipped_when_already_in_req_state`
    — `req_state.get` returns a row → `engine.step` is not called.
  - new `test_orphan_intent_analyze_skipped_when_analyze_tag_present`
    — issue already has both `intent:analyze` and `analyze` (mid- or
    post-flight) → no trigger.
  - new `test_orphan_intent_analyze_skipped_when_status_done` — issue
    is BKD status `done` → no trigger (user already terminated it).
  - new `test_sync_once_runs_orphan_pass_without_obs_pool` — locks in
    that orphan recovery still runs when `obs_pool is None` (the only
    behaviour change in `main.py`'s gate).

## Impact

- **Affected specs**: new capability `snapshot-orphan-trigger` (purely
  additive — no other capability mentions snapshot's role in the entry
  path today).
- **Affected code**:
  - `orchestrator/src/orchestrator/snapshot.py` — refactor `sync_once`,
    add `_trigger_orphan_intent_analyze` helper, change return type.
  - `orchestrator/src/orchestrator/main.py` — drop `obs_pg_dsn` from the
    snapshot start gate.
  - `orchestrator/tests/test_snapshot.py` — update existing assertions,
    add the four orphan-recovery cases.
- **Deployment**: zero ops. Default `SISYPHUS_SNAPSHOT_INTERVAL_SEC`
  remains the trigger; operators who explicitly set the interval to `0`
  keep recovery off. Existing `snapshot_exclude_project_ids` filter
  applies to orphan scan as well — excluded projects are not recovered,
  which matches their "do not touch" semantics.
- **Bootstrap caveat**: orphan recovery walks
  `SELECT DISTINCT project_id FROM req_state`, so the very first REQ in
  a brand-new project must still come through the webhook (we have no
  other source of project IDs). After the first REQ exists, all
  subsequent missed `intent:analyze` webhooks for that project recover
  on the next snapshot tick. Documented in the spec.
- **Risk**: low. `insert_init`'s `ON CONFLICT DO NOTHING` plus
  `cas_transition`'s state guard make the trigger race-safe against a
  delayed real webhook. `start_analyze` is `idempotent=True` and
  rebrands the BKD issue (adding `analyze` and the `REQ-*` tag) so a
  second snapshot pass — even if the action already ran — sees the
  issue as "no longer orphan" and skips.
- **Out of scope**:
  - Recovery for `intent:intake` — same mechanism would work, but the
    failure mode is rarer (user explicitly chooses intake) and we want
    to validate the analyze path first.
  - Per-project bootstrap (seeding the snapshot loop with project IDs
    not yet in `req_state`).
  - Surfacing orphan triggers in Metabase Q1-Q13 dashboards — already
    visible in `event_log` via the existing `router.decision` writes
    that `engine.step` emits.
