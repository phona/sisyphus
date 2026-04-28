# Tasks: REQ-openspec-changes-cleanup-1777343379

## Stage: implementation

- [x] Fix A: Add `_cleanup_openspec_changes_in_runner()` to `escalate.py` — runs rm+commit in runner pod for each involved repo, fail-open
- [x] Fix D: Add `_supersede_stale_openspec_changes()` to `start_analyze.py` — moves same-slug old dirs to `_superseded/` before dispatch, fail-open
- [x] Fix B: Create `orchestrator/scripts/cleanup_orphan_openspec_changes.py` — one-time cleanup script with dry-run + --apply modes, asyncpg PG query
- [x] Fix ruff lint: update `test_actions_start_analyze.py` exec_fn assertions to handle multiple exec_in_runner calls
- [x] Fix pre-existing ruff issues: dispatch_idempotency_challenger + verifier_infra_flake_retry_challenger + test_actions_escalate_openspec_cleanup
- [x] Fix pre-existing docs drift: `docs/state-machine.md` Event count 31→32

## Stage: tests

- [x] `test_actions_escalate_openspec_cleanup.py` — OSC-S1 (cleanup called), OSC-S2 (fail-open on exec error), OSC-S3 (no repos = no exec), OSC-S4 (no controller = fail-open)
- [x] `test_actions_start_analyze_supersede.py` — SUPR-S1 (vN triggers supersede), SUPR-S2 (no-vN = no mv), SUPR-S3 (fail-open on exec error)
- [x] `test_scripts_cleanup_orphan_openspec.py` — COP-S1 done→delete, COP-S2 escalated→delete, COP-S3 in-flight→keep, COP-S4 not-found→delete, COP-S5 archive+_superseded skipped, COP-S6 mixed batch

## Stage: PR

- [x] openspec/changes/REQ-openspec-changes-cleanup-1777343379/ created
- [x] git push feat/REQ-openspec-changes-cleanup-1777343379
- [x] gh pr create
