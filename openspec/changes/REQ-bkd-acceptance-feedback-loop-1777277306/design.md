# Design

## Why "BKD-native only" for Phase 1

`docs/user-feedback-loop.md` §2.3 lists 8 event sources for resolving
`PENDING_USER_PR_REVIEW`. They split into two channels:

- **GH PR review channel** (`pull_request_review` / `pull_request closed`):
  needs new webhook plumbing — the orchestrator currently has no GH webhook
  endpoint, only BKD.
- **BKD channel** (`comment.created` / `issue.updated` statusId): rides on
  the existing `/bkd-events` endpoint.

This REQ ("Case 2") implements the BKD channel. Reasons:

1. **Smallest viable surface that closes the user loop.** No new webhook
   endpoint, no GitHub auth/secret rotation. Stretches the existing
   `issue.updated` semantics one more step.
2. **Matches recent direction.** `REQ-bkd-hitl-end-to-end-loop-1777273753`
   (PR #163) and `REQ-ux-bkd-native-resume-close-1777257392` already
   leaned on BKD chat for HITL signaling.
3. **The user is already on the intent issue.** The acceptance report
   needs to be visible alongside the conversation history they had with
   the intake/analyze agents anyway.

Case 1 (GH PR channel) is a parallel REQ — both can land later because
they emit into the same `ACCEPT_USER_*` event space.

## Why three explicit tags instead of comment parsing

The original design (`docs/user-feedback-loop.md` §2.3) called for
`comment.created` parsing of approve / fix / etc. BKD ≥0.0.65 does send
that webhook event, but parsing free-form chat for an
`/sisyphus approve`-style sentinel is brittle:

- Users forget the prefix.
- Bots / agent-generated follow-ups clutter the same chat.
- A typo in the comment silently does nothing.

Three explicit tags (`acceptance:approve`, `acceptance:request-changes`,
`acceptance:reject`) are the same affordance the user already uses for
`intent:intake` / `intent:analyze`. The BKD tag chip UI makes it a
two-click affirmative action. Free-form feedback still lands as chat
follow-ups (the BKD UI will call `follow_up_issue`); we read those when
fetching `verifier_reason` for the fixer prompt.

## Why pre-populate ctx and reuse `start_fixer` instead of new action

`start_fixer` already handles `verifier_fixer / verifier_scope /
verifier_reason / parent-stage` properly — it tags the new fixer issue
with `parent-stage:<stage>` and embeds the reason text into the bugfix
prompt. The only thing missing for our case is *who pre-populates the ctx
keys.*

In the verifier-driven path, `webhook.py` parses the verifier's decision
JSON and writes `ctx.verifier_*`. In our case, the **webhook itself** —
when it sees `acceptance:request-changes` on the intent issue — performs
the equivalent: fetches the latest user-authored chat from the intent
issue and writes `ctx.verifier_reason` + `verifier_fixer="dev"` +
`verifier_stage="accept"` before invoking `engine.step` with
`ACCEPT_USER_REQUEST_CHANGES`.

`start_fixer` then runs unchanged: same parent-stage tagging, same
bugfix prompt rendering, same round cap, same fixer-issue creation. This
is the smallest viable code path; no new prompt template, no new BKD
issue type, no new fixer audit category.

## Why `ACCEPT_USER_REJECTED` is hard-escalate, not auto-resume

The `escalate.py` heuristic auto-resumes "transient" reasons (session
crashes, watchdog stuck, action errors) up to `_MAX_AUTO_RETRY=2`. User
rejection is **not** transient by definition — the human said "no, drop
it". We set `ctx.escalated_reason="user-rejected-acceptance"`; this falls
into `_is_transient → False` (not in `_TRANSIENT_REASONS`, body.event is
`issue.updated` which is not in `_CANONICAL_SIGNALS`), so escalate goes
straight to terminal `ESCALATED + reason:user-rejected-acceptance` tag.
The user can resume later via the existing
`(ESCALATED, VERIFY_PASS / VERIFY_FIX_NEEDED / VERIFY_ESCALATE)`
follow-up channel by tagging the verifier issue (out of scope for this
REQ).

## Why `acceptance:pending` is a tag, not a `ReqState`-derived constant

External dashboards (Metabase) read BKD tags directly via
`bkd_snapshot`. Tags are the contract surface. `PENDING_USER_ACCEPT` is
a sisyphus-internal `ReqState`. Setting an explicit `acceptance:pending`
tag on the BKD intent issue when entering this state lets dashboards
distinguish "in user review" REQs without having to join into
`req_state.state`.

## Watchdog: minimal surgical change

`docs/user-feedback-loop.md` §1 designed a 4-class taxonomy
(`human-loop-conversation` / `autonomous-bounded` /
`deterministic-checker` / `external-poll`) with per-class timeouts.
That's a separate REQ (`REQ-watchdog-stage-policy-1777269909`, currently
in review on PR #161 — not yet on `main`). Until it lands, this REQ
makes one minimal addition: a module-level `_NO_WATCHDOG_STATES` set
hard-coded to `{PENDING_USER_ACCEPT}`, unioned into the SQL pre-filter
just like `_SKIP_STATES`. When PR #161 lands, the two changes will
naturally merge — both edit the same skip-set primitive.

## Limitation: Phase 1 fixer round does not actually re-run scenarios

This is called out in the proposal but worth being explicit here:

```
PENDING_USER_ACCEPT
  │  user tags acceptance:request-changes + writes feedback
  ▼
FIXER_RUNNING (parent-stage:accept, fixer:dev, reason=feedback)
  │  fixer commits to feat branch
  ▼
REVIEW_RUNNING  ← invoke_verifier_after_fix(stage=accept, trigger=success)
  │  verifier judges fixer's diff against the original spec
  ▼
  pass: apply_verify_pass → _PASS_ROUTING["accept"] = ACCEPT_RUNNING + emit ACCEPT_PASS
  │
  ▼
ACCEPT_TEARING_DOWN (teardown is idempotent — lab already gone)
  │  emit TEARDOWN_DONE_PASS
  ▼
PENDING_USER_ACCEPT (loop) — but the report posted is STALE; no scenarios were re-run.
```

The verifier's "fixer's diff is clean" judgment is the only check
between rounds; no fresh `accept-agent` actually exercises the fix. This
is acceptable for v0 because:

- The original acceptance report is still posted (stale snapshot of last
  scenarios), and the user gets the diff link from the fixer's PR push.
- The user can tag `acceptance:reject` if they want to abort instead of
  iterating.
- Phase 2 will route this differently: a new `ACCEPT_RERUN` event from
  `apply_verify_pass` when `parent-stage=="accept" + user-feedback`
  context, which goes via `create_accept` (lab up + scenarios) instead
  of the short-circuit teardown.

## Test strategy

- `test_state.py`: new transitions added to `EXPECTED` parametrized list.
  `SESSION_FAILED` test grows to include `PENDING_USER_ACCEPT`.
- `test_contract_bkd_acceptance_feedback_loop.py`: new file. Mocks `engine.step`
  through the 4 happy-path transitions; verifies action wiring +
  ctx side effects (e.g. that `verifier_fixer="dev"` is set when
  `ACCEPT_USER_REQUEST_CHANGES` fires).
- `test_watchdog.py`: extend with `PENDING_USER_ACCEPT` row not selected
  by `_tick`.
- `test_webhook_*.py`: extend or add a test for the state-aware routing
  shortcut.
