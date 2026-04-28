## ADDED Requirements

### Requirement: stage_runs records the BKD agent session token for agent stages

The `stage_runs` table SHALL carry a nullable column `bkd_session_id TEXT`
holding the BKD agent's `externalSessionId` (Claude Code session UUID) that
executed that stage. The column MUST be populated for every stage_run whose
stage is in the agent set `{analyze, verifier, fixer, accept, archive}` once
the corresponding BKD session has been assigned an `externalSessionId` (i.e.
by the time the orchestrator processes a `session.completed` or
`session.failed` webhook for that BKD issue). For stages outside the agent
set (`spec_lint`, `dev_cross_check`, `staging_test`, `pr_ci`,
`accept_teardown`), the column MUST remain NULL — those stages have no BKD
agent. A partial index `idx_stage_runs_bkd_session ON
stage_runs(bkd_session_id) WHERE bkd_session_id IS NOT NULL` MUST exist to
support reverse lookup (BKD session id → stage_run row) without paying index
cost on the dominant NULL bucket.

#### Scenario: STR-S1 webhook stamps BKD agent token before engine.step

- **GIVEN** a REQ in state `ANALYZING` with an open `analyze` stage_run
- **WHEN** the webhook receives a `session.completed` event whose BKD issue
  carries `externalSessionId="sess-analyze-uuid"`
- **THEN** `stage_runs.stamp_bkd_session_id(pool, "REQ-x", "analyze",
  "sess-analyze-uuid")` MUST be invoked exactly once, AND the call MUST
  precede the `engine.step` invocation (so the row is still
  `ended_at IS NULL` and the stamp targets the right run)

#### Scenario: STR-S2 mechanical stages skip the stamp

- **GIVEN** a REQ in state `SPEC_LINT_RUNNING`
- **WHEN** the webhook receives a `session.completed` event for a checker
  issue whose BKD payload happens to carry an `externalSessionId`
- **THEN** `stage_runs.stamp_bkd_session_id` MUST NOT be invoked, because
  `spec_lint` is a mechanical checker (not in `AGENT_STAGES`) and writing a
  spurious token to its row would falsely imply an agent ran it

#### Scenario: STR-S3 missing externalSessionId is a no-op stamp

- **GIVEN** a REQ in state `ANALYZING`
- **WHEN** the webhook receives a `session.completed` event whose BKD issue
  payload has `externalSessionId=null` (BKD session not yet assigned a UUID)
- **THEN** `stage_runs.stamp_bkd_session_id` MUST NOT be invoked, so no
  empty / NULL token is written

#### Scenario: STR-S4 session.failed also stamps for crash diagnostics

- **GIVEN** a REQ in state `REVIEW_RUNNING` with an open `verifier` stage_run
- **WHEN** the webhook receives a `session.failed` event whose BKD issue
  carries `externalSessionId="sess-verifier-crashed"`
- **THEN** the webhook MUST fetch the BKD issue (extending the
  session.completed-only fetch path to cover session.failed), AND
  `stage_runs.stamp_bkd_session_id(pool, "REQ-x", "verifier",
  "sess-verifier-crashed")` MUST be invoked, so the crashed run remains
  linkable to its BKD chat for postmortem

### Requirement: stamp_bkd_session_id is idempotent and targets the open row

The `stamp_bkd_session_id` helper in `orchestrator/src/orchestrator/store/stage_runs.py` SHALL only update a stage_run row whose `req_id` and `stage` match the arguments AND whose `ended_at IS NULL` AND whose `bkd_session_id IS NULL`, ordered by `started_at DESC` LIMIT 1, so that already-closed rows are never mutated and an already-set token is never overwritten by a redelivered webhook. The helper MUST treat an empty or falsy `bkd_session_id` argument as a no-op (no SQL emitted, return value `None`).

#### Scenario: STR-S5 stamp targets only ended_at IS NULL AND bkd_session_id IS NULL

- **GIVEN** a `stage_runs` table with multiple rows for `(req_id="REQ-7",
  stage="analyze")`: an older closed row with `bkd_session_id="old-sess"` and
  a newer open row with `bkd_session_id IS NULL`
- **WHEN** `stamp_bkd_session_id(pool, "REQ-7", "analyze", "new-sess")` runs
- **THEN** the SQL MUST contain `ended_at IS NULL` AND `bkd_session_id IS
  NULL` in its WHERE clause, the older closed row MUST NOT be touched, and
  only the newer open row gets `bkd_session_id` set to `"new-sess"`

#### Scenario: STR-S6 empty token is a no-op

- **GIVEN** any `stage_runs` table state
- **WHEN** `stamp_bkd_session_id(pool, "REQ-1", "analyze", "")` is called
- **THEN** no SQL is emitted (return value is None) — saves a round-trip and
  prevents writing an empty string

### Requirement: Issue dataclass exposes externalSessionId from BKD payload

The orchestrator's `Issue` dataclass (shared by REST and MCP transports) SHALL
expose `external_session_id: str | None` populated by `_to_issue` from the
BKD payload field `externalSessionId`. The field MUST default to `None` when
the BKD payload omits it (e.g. immediately after `create_issue`, before BKD
assigns a Claude Code session UUID), and MUST surface the assigned UUID on
subsequent `get_issue` calls once the session has started.

#### Scenario: STR-S7 _to_issue extracts externalSessionId

- **GIVEN** a BKD issue payload dict containing `"externalSessionId":
  "a742034b-6fb0-4047-be96-d5431dc1f252"`
- **WHEN** `_to_issue(payload)` runs
- **THEN** the returned `Issue.external_session_id` MUST equal
  `"a742034b-6fb0-4047-be96-d5431dc1f252"`

#### Scenario: STR-S8 _to_issue defaults external_session_id to None

- **GIVEN** a BKD issue payload dict that omits the `externalSessionId` field
  (e.g. fresh `create_issue` response before session start)
- **WHEN** `_to_issue(payload)` runs
- **THEN** the returned `Issue.external_session_id` MUST be `None` (no
  KeyError)
