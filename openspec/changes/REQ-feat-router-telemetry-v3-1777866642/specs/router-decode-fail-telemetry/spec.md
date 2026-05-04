# Spec delta — router-decode-fail-telemetry

## ADDED Requirements

### Requirement: webhook SHALL persist a stage_runs row on terminal verifier decode-fail

The orchestrator MUST insert a closed `stage_runs` row recording the
decode-fail event whenever `webhook` receives a `session.completed` from a
verifier sub-issue and `router.derive_verifier_event_with_retry_info`
ultimately returns `Event.VERIFY_ESCALATE` with a non-empty `reason` (i.e.
the agent will not be auto-retried — either `retry_worthy=False` initially,
or the per-REQ retry counter has already reached the cap). The row SHALL
set `stage="router_decode_fail"`, `outcome="silent_drop"`, and `fail_reason`
to the router reason string, and `context` SHALL be a JSON object that
contains at minimum `issue_id` (the BKD verifier issue id), `raw_tags` (the
tags array seen at decode time), and `verifier_stage` (the stage parsed from
the `verify:` tag, or `"unknown"` when absent). The row MUST be self-closing
(`started_at` equal to `ended_at` modulo clock drift) so that downstream
stage_runs queries do not treat it as an open-ended run. Insert failure (DB
hiccup) MUST be caught and logged at WARNING; it MUST NOT abort the
surrounding webhook flow.

#### Scenario: RDFT-S1 stage_runs row written when no decision JSON is found
- **GIVEN** a `session.completed` from a verifier issue tagged `["verifier", "verify:staging_test", "REQ-x"]`
- **AND** `extract_decision_robust` returns `decision=None` with `retry_worthy=False`
- **WHEN** the webhook processes the event
- **THEN** exactly one `stage_runs` row is inserted with `stage="router_decode_fail"`, `outcome="silent_drop"`, `fail_reason` matching the router reason string (e.g. starts with `"no decision JSON"`)
- **AND** the row's `context` JSON contains `issue_id`, `raw_tags` (a list including `"verifier"`), and `verifier_stage="staging_test"`
- **AND** the row is closed (`ended_at` is not null)

### Requirement: webhook SHALL surface decode-fail on the BKD verifier issue

On terminal verifier decode-fail, the orchestrator MUST best-effort PATCH the
BKD verifier issue with two visible signals: (a) append a `router-decode-fail`
tag to the issue's `tags` (additive, preserving all existing tags via the
`GET → merge → PATCH` convention used elsewhere in this codebase), and (b)
attempt to append a human-readable warning block to the issue `description`
that includes the router `reason`, the expected decision-tag formats, the raw
tags observed at decode time, and an operator hint on how to recover (manual
PATCH of a `decision:<action>` tag, or follow-up that re-emits the JSON
block). Either step's failure (BKD down, version that does not support
description update) MUST be isolated by try/except and logged at WARNING; the
remaining signals (stage_runs row, log.warning) MUST still fire.

#### Scenario: RDFT-S2 BKD issue gets `router-decode-fail` tag and warning text appended
- **GIVEN** a verifier `session.completed` whose decision parse ultimately escalates with reason `"invalid decision: invalid action: 'pass'"`
- **WHEN** the webhook calls `_emit_decode_fail_telemetry` for that issue
- **THEN** the BKD client receives exactly one `update_issue` call carrying both `tags` (the previous tags plus `"router-decode-fail"`, no duplicates) and `description` (a string containing the literal `router decode 失败` plus the router reason verbatim)
- **AND** when the BKD `update_issue` call raises, the function still returns normally (does not propagate the exception out of the webhook handler) and the in-memory log captures a WARNING with key `router.decode_fail.bkd_patch_failed`

### Requirement: webhook SHALL log decode-fail at WARNING level with structured fields

The orchestrator MUST emit a single `log.warning` with event key
`router.decode_fail` for every terminal decode-fail. The log call SHALL bind
at least `issue_id`, `req_id`, `stage`, `reason`, and `raw_tags` so that
loki / structlog consumers can route on each independently. The log MUST fire
*before* any best-effort downstream call (stage_runs insert / BKD PATCH) so
that even a hard exception in those paths still leaves the WARNING line in
the log stream.

#### Scenario: RDFT-S3 WARNING log line emitted with full context
- **GIVEN** a verifier session.completed that fails decode with reason `"no decision JSON found in tag or description"`
- **WHEN** `_emit_decode_fail_telemetry` runs for that event
- **THEN** the structlog stream contains exactly one entry at level WARNING with event `"router.decode_fail"`
- **AND** that entry binds `issue_id`, `req_id`, `stage`, `reason`, and `raw_tags` keys, where `reason` matches the router reason verbatim and `raw_tags` is the original tags list
