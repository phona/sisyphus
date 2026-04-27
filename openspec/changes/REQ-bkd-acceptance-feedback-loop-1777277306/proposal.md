# BKD acceptance feedback loop (Case 2: PENDING + comment routing + fixer)

## Why

Today sisyphus runs `accept-agent` (lab up + scenarios) and, on
`TEARDOWN_DONE_PASS`, jumps straight to `ARCHIVING` and merges the PR. **The
human user never gets to say "yes that matches what I asked for"** — sisyphus
self-judges acceptance entirely from agent-reported scenario passes.

`docs/user-feedback-loop.md` (commit 2e71a59) already specs the design for a
`PENDING_USER_PR_REVIEW` state that pauses sisyphus between `accept` and
`archive` so the user can approve / request changes / reject. That doc lists 4
M0 sub-REQs; one of them is the user-facing review loop.

This REQ is **case 2 of that design** — the **BKD-native channel only**:
the user signals via the BKD intent issue (tag flips + chat follow-up), not
GitHub PR review webhooks. Rationale: matches the broader
"BKD-native UX" direction (`REQ-ux-bkd-native-resume-close`,
`REQ-bkd-hitl-end-to-end-loop`) and reuses the existing `issue.updated`
webhook surface — no new webhook plumbing needed.

(Case 1 — GH PR review webhook channel — is a separate REQ that this REQ
does NOT block. Both channels can coexist later because they ultimately emit
the same `ACCEPT_USER_*` events into the same `PENDING_USER_ACCEPT` state.)

## What Changes

### State machine

- New state `PENDING_USER_ACCEPT` between `ACCEPT_TEARING_DOWN` and
  `ARCHIVING`.
- New events: `ACCEPT_USER_APPROVED`, `ACCEPT_USER_REQUEST_CHANGES`,
  `ACCEPT_USER_REJECTED`.
- Re-route `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)`: instead of going
  straight to `ARCHIVING + done_archive`, advance to `PENDING_USER_ACCEPT`
  with new action `post_acceptance_report` that publishes a sisyphus-managed
  block on the BKD intent issue describing the scenarios + how the user
  responds.
- 4 new transitions out of `PENDING_USER_ACCEPT`:
  - `ACCEPT_USER_APPROVED → ARCHIVING (done_archive)`
  - `ACCEPT_USER_REQUEST_CHANGES → FIXER_RUNNING (start_fixer)` — webhook
    pre-populates `ctx.verifier_stage="accept" / verifier_fixer="dev" /
    verifier_reason=<user comment>` so the existing `start_fixer` action
    works as-is.
  - `ACCEPT_USER_REJECTED → ESCALATED (escalate, reason=user-rejected-acceptance)`
  - `SESSION_FAILED → PENDING_USER_ACCEPT (escalate self-loop)` — defensive,
    matches the rest of the running states.

### Webhook routing (`webhook.py`)

A state-aware shortcut: when `cur_state == PENDING_USER_ACCEPT` AND
`body.event == "issue.updated"` AND it's the intent issue, look at
`body.tags` / `body.changes.statusId` directly to decide which
`ACCEPT_USER_*` event to emit. This bypasses `derive_event` because that
helper has no notion of `cur_state` and the same set of intent-issue tags
(e.g. `acceptance:approve`) means nothing outside this state.

Tag conventions on the intent issue (user adds these via BKD UI):

| user tag | derived event | next state |
|---|---|---|
| `acceptance:approve` | `ACCEPT_USER_APPROVED` | `ARCHIVING` |
| `acceptance:request-changes` | `ACCEPT_USER_REQUEST_CHANGES` | `FIXER_RUNNING` |
| `acceptance:reject` | `ACCEPT_USER_REJECTED` | `ESCALATED` |

Plus: BKD `statusId` flipped to `done` while in `PENDING_USER_ACCEPT` (and
no `acceptance:approve` tag set) is also treated as `ACCEPT_USER_REJECTED`,
matching the design doc's "user closed the issue → rejected" semantic.

### `post_acceptance_report` action

- Renders the sisyphus-managed block from the accept agent's last
  assistant message + the scenarios it ran, and PATCHes the intent issue
  body to include it.
- Sets the BKD intent issue tags `acceptance:pending` (replacing `accept`
  if present) so dashboards can count "REQs waiting on user".
- Sets `statusId=review` so the BKD board "review" column surfaces these
  REQs to the user.
- Persists the rendered report into `ctx.acceptance_report` so a later
  fixer round can use it as context.

### Watchdog

`PENDING_USER_ACCEPT` MUST NOT be watchdogged. It's a human-loop state
that can sit for hours / days. A `_NO_WATCHDOG_STATES` skip set in
`watchdog.py` excludes it from the SQL pre-filter.

## Impact

- Affected specs: new capability `bkd-acceptance-feedback-loop`
- Affected code:
  - `orchestrator/src/orchestrator/state.py` (new state + events + transitions)
  - `orchestrator/src/orchestrator/webhook.py` (state-aware routing)
  - `orchestrator/src/orchestrator/actions/__init__.py` (register new action)
  - `orchestrator/src/orchestrator/actions/post_acceptance_report.py` (new file)
  - `orchestrator/src/orchestrator/watchdog.py` (skip set for human-loop states)
  - `orchestrator/src/orchestrator/engine.py` (`STATE_TO_STAGE` adds
    `PENDING_USER_ACCEPT → "pending_user_accept"`)
  - `orchestrator/src/orchestrator/prompts/_pending_user_accept.md.j2` (new
    Jinja template for the intent-issue block)
  - `orchestrator/tests/test_state.py` (new transitions in EXPECTED)
  - `orchestrator/tests/test_contract_bkd_acceptance_feedback_loop.py` (new file)
- No DB migration needed — `ctx` columns already accept arbitrary jsonb keys.
- Backwards compat: any in-flight REQ that was at `ACCEPT_TEARING_DOWN` when
  this ships will, on the next `TEARDOWN_DONE_PASS`, advance to
  `PENDING_USER_ACCEPT` instead of `ARCHIVING`. That's intentional — the new
  pause is the feature. Operators can manually tag `acceptance:approve` to
  unblock them.

## Out of scope (Phase 2 follow-ups)

- GitHub `pull_request_review` webhook channel (Case 1).
- After a user-feedback fixer round + verifier pass, the verifier currently
  routes back to `ACCEPT_RUNNING + emit ACCEPT_PASS`, which short-circuits
  the lab through a stale teardown. **This means user-feedback re-runs do
  NOT actually re-execute scenarios in Phase 1**; the verifier's "fix
  looks good" judgment alone drives the loop back to `PENDING_USER_ACCEPT`
  with a stale acceptance report. Phase 2 will add a fresh-rerun path.
- A `daily-digest` of REQs sitting in `PENDING_USER_ACCEPT > 24h`.
