# feat(obs): write BKD agent token to stage_runs

## Why

`stage_runs` currently records who ran what stage (req_id, stage, agent_type,
model, started/ended timestamps, outcome) but **does not record which BKD
session actually executed it**. The Metabase dashboards driven off `stage_runs`
(M14e + M7) can compute aggregate stats — pass rate, p50/p95 duration, fixer
churn — but a row that says "REQ-foo's `analyze` stage ran for 12 minutes and
escalated" gives no path to inspect what that agent was thinking. Pulling up
the corresponding BKD chat today requires:

1. Eyeballing the timestamp range
2. Browsing `bkd_snapshot` for the right `parent_issue_id` chain
3. Cross-referencing tags by hand to find the BKD issue
4. Opening the BKD UI and clicking through to the session log

That manual lookup is the dominant friction in the "which prompt should I
change" feedback loop the observability stack is supposed to enable.

The BKD `Issue` payload already exposes `externalSessionId` — the Claude Code
session UUID assigned to that agent's run. Storing it on the matching
`stage_runs` row gives:

- A direct join key from `stage_runs` → BKD session log (the BKD UI URL is
  derivable from project + session id; Metabase can render it as a link column)
- Reverse lookup: paste a BKD session id, find which REQ / stage / outcome it
  produced, without scrolling through chat history first
- Audit trail: which exact agent run produced a given fixer-round / verifier
  decision, which is the unit the "agent quality" view in
  `agent_quality.sql` reasons about

This is purely additive observability metadata — the state machine, action
handlers, and webhook routing logic are unchanged.

## What Changes

- **`orchestrator/migrations/0008_stage_runs_bkd_session_id.sql`** — add
  nullable `bkd_session_id TEXT` column to `stage_runs` plus a partial index
  `idx_stage_runs_bkd_session ON stage_runs(bkd_session_id) WHERE bkd_session_id
  IS NOT NULL` (mechanical stages stay NULL — the partial index avoids
  indexing the large NULL bucket). Rollback drops both.
- **`orchestrator/src/orchestrator/bkd.py`** — `Issue` dataclass gains
  `external_session_id: str | None = None`; `_to_issue` populates it from the
  BKD payload's `externalSessionId` field (kept on the shared dataclass so
  both REST and MCP transports surface it).
- **`orchestrator/src/orchestrator/store/stage_runs.py`** — new
  `stamp_bkd_session_id(pool, req_id, stage, bkd_session_id)` helper. UPDATEs
  the most-recent (req_id, stage) row whose `ended_at IS NULL AND
  bkd_session_id IS NULL` — idempotent (won't overwrite an existing token,
  won't touch closed rows). Returns the row id stamped, or None.
- **`orchestrator/src/orchestrator/engine.py`** — promote the existing
  module-private `_STATE_TO_STAGE` to public `STATE_TO_STAGE`; export new
  `AGENT_STAGES = frozenset({"analyze", "verifier", "fixer", "accept",
  "archive"})` so the webhook can decide whether the current stage is BKD-agent
  driven (worth stamping) or mechanical (skip).
- **`orchestrator/src/orchestrator/webhook.py`** — extend the existing BKD
  fetch to also cover `session.failed` (so crashed agents are still linked to
  their session). After resolving `cur_state`, before calling `engine.step`,
  if the fetched `Issue.external_session_id` is non-null and `cur_stage` is in
  `AGENT_STAGES`, call `stage_runs.stamp_bkd_session_id(...)`. Best-effort:
  exceptions are logged and swallowed so observability writes never fail
  webhook processing.
- **Tests** — extend `test_store_stage_runs.py` with stamp-helper unit tests;
  extend `test_bkd_rest.py` with `_to_issue` parsing tests; new
  `test_contract_stage_runs_token_tracking.py` covering 4 webhook scenarios
  (STR-S1 through STR-S4).

## Impact

- **Affected specs**: new capability `stage-runs-token-tracking` (purely
  additive — no existing requirement is modified or removed).
- **Affected code**: `orchestrator/migrations/0008_stage_runs_bkd_session_id.{sql,rollback.sql}`;
  `orchestrator/src/orchestrator/{bkd,engine,webhook}.py`;
  `orchestrator/src/orchestrator/store/stage_runs.py`;
  `orchestrator/tests/{test_store_stage_runs,test_bkd_rest,test_contract_stage_runs_token_tracking}.py`.
- **Deployment / migration**: low-risk schema delta — the column is nullable,
  yoyo applies it on orchestrator boot. No backfill: pre-existing rows stay
  NULL (they're already historical agent runs whose BKD sessions may be GC'd
  anyway). Roll-forward only; rollback drops the column safely.
- **Risk**: low. Stamp is best-effort observability — failures logged and
  swallowed. Mechanical stages explicitly skipped via `AGENT_STAGES` so the
  contract is "agent stages may have a token; mechanical stages never do".
  The webhook fetch path is extended to also cover `session.failed`, costing
  one extra HTTP round-trip per agent crash; acceptable given the diagnostic
  value.
- **Out of scope**: backfilling historical `stage_runs` rows (BKD session
  retention isn't long enough to make that useful); rendering Metabase link
  columns (separate dashboard PR using the new column); recording BKD session
  ids for `intake` / `challenger` stages (engine doesn't currently open
  `stage_runs` rows for them — addressing that is a separate change).
