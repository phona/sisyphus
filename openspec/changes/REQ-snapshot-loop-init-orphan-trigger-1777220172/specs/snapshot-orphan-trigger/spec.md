## ADDED Requirements

### Requirement: snapshot loop recovers orphan intent:analyze BKD issues

The orchestrator's periodic `snapshot.sync_once` SHALL, in addition to its
existing `bkd_snapshot` observability UPSERT, scan every BKD issue it
fetches for **orphan `intent:analyze` entries** and trigger the missed
`INTENT_ANALYZE` event itself. The scan MUST run on every tick of
`snapshot.run_loop`, regardless of whether the observability database is
configured. An issue MUST be classified as an orphan if and only if all
the following hold:

1. its tags include the literal string `intent:analyze`,
2. its tags do **not** include the literal string `analyze` (i.e. the
   normal entry action has not yet rebranded the issue),
3. its `statusId` is neither `done` nor `cancelled`,
4. a `req_id` is resolvable from its tags (`REQ-*` match) or — when no
   such tag exists — by falling back to `REQ-{issueNumber}`, mirroring
   the webhook's `extract_req_id` rule, and
5. `req_state.get(pool, req_id)` returns `None` (no row yet exists for
   that REQ).

For each orphan, the loop MUST run the same recovery sequence the webhook
handler would have run:

1. `req_state.insert_init(pool, req_id, project_id, context={"intent_issue_id": issue.id, "intent_title": issue.title.strip(), "snapshot_recovered": True})`,
2. re-`get` the row to obtain its current state (defends against a
   concurrent webhook beating the snapshot to the insert), then
3. `engine.step(pool, body=<webhook-shaped body>, req_id=..., project_id=..., tags=..., cur_state=row.state, ctx=row.context, event=Event.INTENT_ANALYZE)`.

The synthesized `body` MUST expose the attributes
`engine.step`/`actions/start_analyze.py` read: `event="issue.updated"`,
`issueId`, `issueNumber`, `projectId`, `title`, `tags` (a list copy),
`executionId=None`, `finalStatus=None`, `changes=None`,
`timestamp=None`. Recovery MUST NOT raise out of the loop: any exception
during a single issue's recovery MUST be caught, logged at WARNING with
the `req_id`, `issue_id`, and `project_id`, and MUST NOT prevent the
remaining issues — or other projects — from being scanned.

The list of projects scanned by the orphan pass MUST be the same one
already used for the observability UPSERT: `SELECT DISTINCT project_id
FROM req_state`, filtered through `settings.snapshot_exclude_project_ids`.
Bootstrapping a brand-new project (zero `req_state` rows for that
project) is therefore intentionally **out of scope** — the first REQ
must arrive via webhook to seed the project ID; every subsequent missed
`intent:analyze` webhook for that project recovers on the next tick.

`snapshot.sync_once` SHALL keep its existing `int` return type (number of
rows UPSERTed into `bkd_snapshot`) so the orch-noise-cleanup contract
(`ORCHN-S3`: returns `0` when every project_id is excluded) still
holds. The number of orphan triggers is reported through the
`snapshot.synced` log line as `orphans_triggered=<n>`; tests assert on
`engine.step` / `req_state.insert_init` mocks rather than the return
value.

`main.py`'s startup MUST start `snapshot.run_loop` whenever
`settings.snapshot_interval_sec > 0`, independent of `obs_pg_dsn`, so
that orphan recovery runs on deployments without an observability
database.

#### Scenario: SNAP-ORPHAN-S1 orphan intent:analyze triggers INTENT_ANALYZE

- **GIVEN** BKD list returns one issue with `id="i-9"`, `issueNumber=9`,
  `statusId="working"`, `tags=["intent:analyze"]`, no matching row in
  `req_state` for `REQ-9`
- **WHEN** `snapshot.sync_once()` runs for that project
- **THEN** `req_state.insert_init` is called once with
  `req_id="REQ-9"`, `project_id=<the project id>`, and `context`
  containing `intent_issue_id="i-9"` and `snapshot_recovered=True`
- **AND** `engine.step` is called once with `event=Event.INTENT_ANALYZE`,
  `cur_state=ReqState.INIT`, and a body whose `issueId="i-9"`,
  `projectId=<the project id>`, and `tags` contain `"intent:analyze"`

#### Scenario: SNAP-ORPHAN-S2 issue already tracked in req_state is skipped

- **GIVEN** a BKD issue with `tags=["intent:analyze", "REQ-42"]` and
  `req_state.get(pool, "REQ-42")` returns a non-None row
- **WHEN** `snapshot.sync_once()` runs
- **THEN** `req_state.insert_init` MUST NOT be called for `REQ-42` and
  `engine.step` MUST NOT be called

#### Scenario: SNAP-ORPHAN-S3 issue already past entry (analyze tag) is skipped

- **GIVEN** a BKD issue with `tags=["intent:analyze", "analyze",
  "REQ-7"]` (the entry action has already rebranded it) and
  `req_state.get(pool, "REQ-7")` returns `None`
- **WHEN** `snapshot.sync_once()` runs
- **THEN** `engine.step` MUST NOT be called — the snapshot loop refuses
  to revive a REQ whose entry action ran and then somehow lost its
  `req_state` row (operator cleanup / DB reset territory)

#### Scenario: SNAP-ORPHAN-S4 issue in BKD status done is skipped

- **GIVEN** a BKD issue with `tags=["intent:analyze"]` and
  `statusId="done"` (the user explicitly closed it before the webhook
  could be processed)
- **WHEN** `snapshot.sync_once()` runs
- **THEN** `engine.step` MUST NOT be called

#### Scenario: SNAP-ORPHAN-S5 orphan recovery runs when obs pool is absent

- **GIVEN** `db.get_obs_pool()` returns `None` (observability database
  not configured) but `db.get_pool()` returns a working main pool with
  at least one project_id row
- **WHEN** `snapshot.sync_once()` runs and the project's BKD list
  contains exactly one orphan issue as in SNAP-ORPHAN-S1
- **THEN** `engine.step` is still called once and the `int` return of
  `sync_once()` is `0` (no rows go to `bkd_snapshot` because the obs
  pool is absent)
