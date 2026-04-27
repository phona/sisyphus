# Tasks

## Stage: contract / spec

- [x] author specs/bkd-acceptance-feedback-loop/spec.md (delta scenarios BAFL-S1..S8)
- [x] author proposal.md
- [x] author design.md (rationale for BKD-only / 3-tags / ctx pre-population)

## Stage: implementation

- [x] state.py: `ReqState.PENDING_USER_ACCEPT` enum value
- [x] state.py: 3 new events `ACCEPT_USER_APPROVED` / `ACCEPT_USER_REQUEST_CHANGES` / `ACCEPT_USER_REJECTED`
- [x] state.py: re-route `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` → `PENDING_USER_ACCEPT` (action `post_acceptance_report`)
- [x] state.py: 4 new transitions out of `PENDING_USER_ACCEPT`
- [x] state.py: extend SESSION_FAILED self-loop dict comprehension to cover `PENDING_USER_ACCEPT`
- [x] engine.py: `STATE_TO_STAGE[PENDING_USER_ACCEPT] = "pending_user_accept"`
- [x] actions/post_acceptance_report.py: new file, registered as `post_acceptance_report`
- [x] actions/__init__.py: import the new module so REGISTRY picks it up
- [x] webhook.py: state-aware routing shortcut (PENDING_USER_ACCEPT → 3 ACCEPT_USER_* events; statusId=done fallback for reject)
- [x] webhook.py: when emitting `ACCEPT_USER_REQUEST_CHANGES`, fetch latest user message from BKD intent issue chat and stash into `ctx.verifier_*` so `start_fixer` works
- [x] watchdog.py: `_NO_WATCHDOG_STATES = {PENDING_USER_ACCEPT}` unioned into SQL pre-filter
- [x] prompts/_pending_user_accept.md.j2 (rendered by `post_acceptance_report` for the BKD intent issue body)

## Stage: tests

- [x] test_state.py: 4 new entries in `EXPECTED` parametrized list
- [x] test_state.py: SESSION_FAILED self-loop test extended to include `PENDING_USER_ACCEPT`
- [x] test_state.py: rerouted `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` updated (`ARCHIVING/done_archive` → `PENDING_USER_ACCEPT/post_acceptance_report`)
- [x] test_contract_bkd_acceptance_feedback_loop.py: 8 contract scenarios per spec.md
- [x] test_watchdog.py: PENDING_USER_ACCEPT excluded from sweep

## Stage: PR

- [x] git push feat/REQ-bkd-acceptance-feedback-loop-1777277306
- [x] gh pr create --label sisyphus + sisyphus cross-link footer
