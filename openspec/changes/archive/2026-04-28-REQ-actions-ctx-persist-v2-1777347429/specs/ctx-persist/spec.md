## ADDED Requirements

### Requirement: start_challenger MUST persist challenger_issue_id into REQ context

The `start_challenger` action SHALL call `req_state.update_context` with
`{"challenger_issue_id": <issue_id>}` immediately after a BKD issue is created or
retrieved from the dispatch slug cache. The update MUST occur in both the slug-hit
(idempotent replay) path and the new-issue creation path. This invariant MUST hold so
that the watchdog can reconcile the BKD session status for CHALLENGER_RUNNING via
`_STATE_ISSUE_KEY[CHALLENGER_RUNNING] = "challenger_issue_id"`.

#### Scenario: CTX-S1 new challenger issue persists challenger_issue_id to ctx

- **GIVEN** no slug hit (first dispatch for this REQ+executionId)
- **WHEN** `start_challenger` completes BKD issue creation
- **THEN** `req_state.update_context` is called with `{"challenger_issue_id": <new_issue_id>}`
  before the action returns

#### Scenario: CTX-S2 slug-hit replay also persists challenger_issue_id to ctx

- **GIVEN** a slug hit returning cached `issue_id = "ch-existing-1"`
- **WHEN** `start_challenger` resolves via the slug cache
- **THEN** `req_state.update_context` is called with `{"challenger_issue_id": "ch-existing-1"}`
  before the action returns

### Requirement: watchdog MUST NOT treat absent issue_id as session-ended for autonomous-bounded stages

The watchdog `_check_and_escalate` function SHALL skip escalation when `issue_id` is
`None` and `policy.stuck_sec` is `None` (autonomous-bounded stage). In this case the
function MUST log a warning at level WARNING with key `watchdog.missing_issue_id` and
return without emitting `SESSION_FAILED`. This prevents premature escalation when an
action fails to call `update_context` or when the stage uses direct K8s exec with no
associated BKD issue (e.g. ACCEPT_RUNNING).

Stages with `policy.stuck_sec` set to a non-None value (e.g. PR_CI_RUNNING,
SPEC_LINT_RUNNING, DEV_CROSS_CHECK_RUNNING) SHALL continue to escalate normally even
when `issue_id` is absent, since those stages have explicit time caps and the absent
key indicates a checker-mode stage without a BKD session.

#### Scenario: CTX-S3 CHALLENGER_RUNNING with missing issue_id skips escalation

- **GIVEN** state=CHALLENGER_RUNNING, ctx={} (no challenger_issue_id), stuck_sec=400
- **WHEN** watchdog `_check_and_escalate` runs
- **THEN** no `SESSION_FAILED` event is emitted; `watchdog.missing_issue_id` is logged

#### Scenario: CTX-S4 PR_CI_RUNNING with missing issue_id still escalates

- **GIVEN** state=PR_CI_RUNNING, ctx={} (no pr_ci_watch_issue_id), stuck_sec=2000
- **WHEN** watchdog `_check_and_escalate` runs
- **THEN** `SESSION_FAILED` is emitted (policy.stuck_sec=14400 ≠ None, defensive check skipped)
