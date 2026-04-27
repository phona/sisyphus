## ADDED Requirements

### Requirement: dispatch_slugs table guards BKD issue creation against duplicate POST

The orchestrator MUST maintain a `dispatch_slugs` table that maps a deterministic slug to
a BKD `issue_id`. Before calling `bkd.create_issue()` in any action handler, the handler
SHALL compute a slug and check the table. If a matching slug exists, the handler MUST return
the cached `issue_id` without issuing a new POST to BKD. If no matching slug exists, the
handler SHALL create the issue and insert the slug mapping after the POST succeeds.

The slug MUST be computed as follows:
- `invoke_verifier`: `verifier|{req_id}|{stage}|{trigger}|r{fixer_round}` where
  `fixer_round` is `ctx.get("fixer_round", 0)`
- `start_fixer`: `fixer|{req_id}|{fixer}|r{next_round}`
- All other action handlers: `{action_name}|{req_id}|{body.executionId or ""}`

#### Scenario: DISP-S1 slug hit returns cached issue_id without calling BKD

- **GIVEN** a slug `verifier|REQ-1|spec_lint|fail|r0` already exists in `dispatch_slugs` with `issue_id = "abc123"`
- **WHEN** `invoke_verifier(req_id="REQ-1", stage="spec_lint", trigger="fail", ctx={})` is called
- **THEN** the function returns `{"verifier_issue_id": "abc123", ...}` without calling `bkd.create_issue()`

#### Scenario: DISP-S2 no slug hit proceeds with create_issue and stores slug

- **GIVEN** no slug `verifier|REQ-1|spec_lint|fail|r0` exists in `dispatch_slugs`
- **WHEN** `invoke_verifier(req_id="REQ-1", stage="spec_lint", trigger="fail", ctx={})` is called
- **THEN** `bkd.create_issue()` is called, and afterward the slug mapping is stored in `dispatch_slugs`

#### Scenario: DISP-S3 get returns None for absent slug

- **GIVEN** an empty `dispatch_slugs` table
- **WHEN** `dispatch_slugs.get(pool, "verifier|REQ-1|spec_lint|fail|r0")` is called
- **THEN** the function returns `None`

#### Scenario: DISP-S4 put stores slug and is idempotent on conflict

- **GIVEN** `dispatch_slugs` table (empty or with existing entries)
- **WHEN** `dispatch_slugs.put(pool, "verifier|REQ-1|spec_lint|fail|r0", "abc123")` is called twice
- **THEN** the second call does not raise an error (ON CONFLICT DO NOTHING)

#### Scenario: DISP-S5 round-aware slug distinguishes fixer rounds

- **GIVEN** verifier for round 0 already ran (slug `verifier|REQ-1|spec_lint|fail|r0` exists)
- **WHEN** `invoke_verifier(req_id="REQ-1", stage="spec_lint", trigger="success", ctx={"fixer_round": 1})` is called
- **THEN** a NEW slug `verifier|REQ-1|spec_lint|success|r1` is computed — no slug hit — `create_issue()` is called
