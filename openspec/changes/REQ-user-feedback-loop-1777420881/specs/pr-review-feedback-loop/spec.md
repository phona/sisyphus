# pr-review-feedback-loop

## ADDED Requirements

### Requirement: sisyphus SHALL expose a PENDING_USER_PR_REVIEW state driven by GitHub PR review webhooks

The orchestrator SHALL add a new `ReqState.PENDING_USER_PR_REVIEW` (`pending-user-pr-review`) that replaces `PENDING_USER_REVIEW` as the default post-accept state. When `ACCEPT_TEARING_DOWN` receives `Event.TEARDOWN_DONE_PASS`, the state machine MUST transition to `PENDING_USER_PR_REVIEW` (action `post_acceptance_report`). From `PENDING_USER_PR_REVIEW`, the state machine MUST support three new events derived from GitHub PR review webhooks:

- `GH_PR_REVIEW_APPROVED` (`gh-pr-review.approved`) — review.state == "approved"
- `GH_PR_REVIEW_CHANGES_REQUESTED` (`gh-pr-review.changes-requested`) — review.state == "changes_requested"
- `GH_PR_REVIEW_COMMENTED` (`gh-pr-review.commented`) — review.state == "commented" (or pull_request_review_comment.created)

The transitions from `PENDING_USER_PR_REVIEW` MUST be:
- approved → `ARCHIVING` + `done_archive` action (shortest path, no verifier)
- changes_requested → `REVIEW_RUNNING` + `invoke_verifier_for_pr_review_fail`
- commented → `REVIEW_RUNNING` + `invoke_verifier_for_pr_review_comment`
- PR_MERGED → `ARCHIVING` + `done_archive` (escape hatch)

The old `PENDING_USER_REVIEW` state and its transitions (`USER_REVIEW_PASS` / `USER_REVIEW_FIX`) MUST remain in the transition table for backward compatibility.

#### Scenario: PR-FL-S1 approved review triggers direct archive

- **GIVEN** a REQ in state `PENDING_USER_PR_REVIEW`
- **WHEN** GitHub webhook `pull_request_review.submitted` with `review.state="approved"` arrives
- **THEN** the state machine MUST transition to `ARCHIVING` with action `done_archive`
- **AND** no verifier agent MUST be started

#### Scenario: PR-FL-S2 changes_requested triggers verifier then fixer

- **GIVEN** a REQ in state `PENDING_USER_PR_REVIEW`
- **WHEN** GitHub webhook with `review.state="changes_requested"` arrives
- **THEN** the state machine MUST transition to `REVIEW_RUNNING` with action `invoke_verifier_for_pr_review_fail`
- **AND** the verifier prompt MUST include the review body text

#### Scenario: PR-FL-S3 commented with LGTM treated as approved

- **GIVEN** a REQ in state `PENDING_USER_PR_REVIEW`
- **WHEN** GitHub webhook with `review.state="commented"` and body containing "LGTM" arrives
- **THEN** the derived event MUST be `GH_PR_REVIEW_APPROVED`
- **AND** the state machine MUST transition to `ARCHIVING`

#### Scenario: PR-FL-S4 commented with fix: treated as changes_requested

- **GIVEN** a REQ in state `PENDING_USER_PR_REVIEW`
- **WHEN** GitHub webhook with `review.state="commented"` and body starting with "fix:" arrives
- **THEN** the derived event MUST be `GH_PR_REVIEW_CHANGES_REQUESTED`
- **AND** the state machine MUST transition to `REVIEW_RUNNING`

#### Scenario: PR-FL-S5 unknown review state returns None

- **GIVEN** any review state string not in {approved, changes_requested, commented}
- **WHEN** `_derive_event_from_review_state` is called
- **THEN** it MUST return `None`

### Requirement: sisyphus SHALL expose a /github-events endpoint with HMAC-SHA256 signature verification

The orchestrator SHALL expose `POST /github-events` that accepts GitHub webhook payloads. It MUST verify the `X-Hub-Signature-256` header using HMAC-SHA256 against `settings.github_webhook_secret`. Invalid signatures MUST return HTTP 401. Valid payloads MUST be parsed as JSON. The endpoint MUST handle `pull_request_review.submitted` and `pull_request_review_comment.created` events; all other events MUST be skipped with a logged reason.

The endpoint MUST extract the REQ id from the PR branch name (pattern `feat/REQ-xxx` or `REQ-xxx`). If direct extraction fails, it MUST fall back to querying `req_state` by `context->>'branch'`. It MUST verify that the REQ's current state is `PENDING_USER_PR_REVIEW`; if not, it MUST skip with a logged reason. It MUST deduplicate events using a key derived from `gh_event|review_id|req_id`, writing the dedup record before processing and marking it processed after.

Before calling `engine.step`, it MUST write `gh_pr_review_state`, `gh_pr_review_body`, and `gh_pr_review_url` into the REQ's context.

#### Scenario: PR-WH-S6 valid signature passes

- **GIVEN** a valid `github_webhook_secret` is configured
- **WHEN** a request arrives with correct `X-Hub-Signature-256`
- **THEN** signature verification MUST succeed

#### Scenario: PR-WH-S7 invalid signature returns 401

- **GIVEN** a valid `github_webhook_secret` is configured
- **WHEN** a request arrives with incorrect `X-Hub-Signature-256`
- **THEN** the endpoint MUST return HTTP 401

#### Scenario: PR-WH-S8 state mismatch skips

- **GIVEN** a REQ whose state is `ARCHIVING` (not `PENDING_USER_PR_REVIEW`)
- **WHEN** a PR review webhook for that REQ arrives
- **THEN** the endpoint MUST return {"action": "skip", "reason": "...not pending-user-pr-review..."}
- **AND** engine.step MUST NOT be called

### Requirement: sisyphus verifier framework SHALL support a pr_review stage

The verifier framework (`actions/_verifier.py`) MUST accept `pr_review` as a valid stage in `_STAGES`. The `_PASS_ROUTING` table MUST map `pr_review` to `(ReqState.ARCHIVING, Event.ARCHIVE_DONE)`, so that `apply_verify_pass` can handle verifier decision=pass for PR review by transitioning directly to ARCHIVING and emitting ARCHIVE_DONE.

The framework MUST expose three action handlers registered in `REGISTRY`:
- `invoke_verifier_for_pr_review_fail` — triggers verifier with stage="pr_review", trigger="fail", stderr_tail from ctx.gh_pr_review_body
- `invoke_verifier_for_pr_review_success` — triggers verifier with stage="pr_review", trigger="success"
- `invoke_verifier_for_pr_review_comment` — triggers verifier with stage="pr_review", trigger="fail" (treated as potential issue)

#### Scenario: PR-VR-S9 pr_review pass routes to archiving

- **GIVEN** a verifier decision with action="pass" for stage="pr_review"
- **WHEN** `apply_verify_pass` executes
- **THEN** it MUST CAS from `REVIEW_RUNNING` to `ARCHIVING`
- **AND** it MUST emit `Event.ARCHIVE_DONE`

#### Scenario: PR-VR-S10 pr_review fail invokes verifier with review body

- **GIVEN** a REQ in state `PENDING_USER_PR_REVIEW` with `ctx.gh_pr_review_body="fix the naming"`
- **WHEN** `invoke_verifier_for_pr_review_fail` executes
- **THEN** it MUST call `invoke_verifier` with `stage="pr_review"`, `trigger="fail"`, `stderr_tail="fix the naming"`

### Requirement: sisyphus watchdog SHALL skip PENDING_USER_PR_REVIEW

The watchdog module MUST include `ReqState.PENDING_USER_PR_REVIEW` in both `_SKIP_STATES` and `_NO_WATCHDOG_STATES` (via `_STAGE_POLICY` with `None`). This ensures the watchdog NEVER emits `SESSION_FAILED` or escalates a REQ that is legitimately waiting for human PR review.

#### Scenario: PR-WD-S11 watchdog ignores pending PR review

- **GIVEN** a REQ in state `PENDING_USER_PR_REVIEW` for 24 hours
- **WHEN** the watchdog tick runs
- **THEN** the REQ MUST NOT appear in the SQL prefilter results
- **AND** no escalation MUST occur

### Requirement: sisyphus post_acceptance_report SHALL notify users about GitHub PR review

The `post_acceptance_report` action MUST update its managed block text to instruct users to use GitHub PR review instead of BKD statusId. The block MUST explain that `Approve` triggers auto-archive, `Request changes` triggers auto-fixer, and `Comment` with specific keywords is interpreted accordingly. The backward-compatible BKD statusId path MUST still be mentioned.

#### Scenario: PR-PA-S12 report includes PR review instructions

- **GIVEN** `post_acceptance_report` runs for a REQ with `pr_urls` recorded
- **WHEN** it patches the BKD intent issue description
- **THEN** the description MUST contain text explaining GitHub PR review as the primary approval mechanism
- **AND** it MUST still mention BKD statusId as a compatible fallback
