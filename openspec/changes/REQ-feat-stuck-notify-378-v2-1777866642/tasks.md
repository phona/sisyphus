# Tasks — REQ-feat-stuck-notify-378-v2-1777866642

owner: analyze-agent

## Stage: spec
- [x] author `specs/watchdog-stuck-notify/spec.md` (delta format, 4 scenarios)
- [x] write `proposal.md` covering Why / What / Out of scope

## Stage: implementation
- [x] add 3 settings in `orchestrator/src/orchestrator/config.py` (`escalated_stale_*`)
- [x] add `_notify_stale_escalated_tick` in `orchestrator/src/orchestrator/watchdog.py`
- [x] add helper `_post_telegram_notify` (best-effort httpx POST; never raises)
- [x] hook tick into `run_loop` at the same 5-tick cadence as bkd_sync
- [x] pass through 3 env vars in `orchestrator/helm/values.yaml`
- [x] pass through 3 env vars in `orchestrator/helm/templates/configmap.yaml`

## Stage: unit tests
- [x] `tests/test_watchdog_stuck_notify.py` — covers WSN-S1 .. WSN-S4

## Stage: PR (推前必须全绿)
- [x] git push feat/REQ-feat-stuck-notify-378-v2-1777866642
- [x] `make ci-lint` → all green
- [x] `make ci-unit-test` → all green
- [x] `make ci-integration-test` → all green (no PG → auto-skip = pass)
- [x] gh pr create with `--label sisyphus` + sisyphus footer
