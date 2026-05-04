# Tasks — REQ-feat-router-telemetry-v3-1777866642

## Stage: contract / spec
- [x] author specs/router-decode-fail-telemetry/spec.md (3 scenarios: RDFT-S1..S3)
- [x] write proposal.md / tasks.md

## Stage: implementation
- [x] add `insert_decode_fail` helper in `orchestrator/src/orchestrator/store/stage_runs.py` writing one closed `stage_runs` row (`stage="router_decode_fail"`, `outcome="silent_drop"`, context with `issue_id` / `raw_tags` / `verifier_stage`)
- [x] add `_emit_decode_fail_telemetry` in `orchestrator/src/orchestrator/webhook.py` performing the 3 emit signals (stage_runs row + BKD tag/description PATCH + log.warning), each best-effort with isolated try/except so any single failure does not block the others
- [x] wire `_emit_decode_fail_telemetry` at every terminal verifier decode-fail point (retry_worthy=False direct escalate; retry exhausted escalate); leave the retry follow_up path untouched (agent gets its self-correction window first)
- [x] add unit tests in `orchestrator/tests/test_webhook_decode_fail_telemetry.py` covering the 3 emit points across both terminal paths

## Stage: PR (gates before push)
- [x] `make ci-lint` green
- [x] `make ci-unit-test` green
- [x] `make ci-integration-test` green (or skipped if no PG)
- [x] git push feat/REQ-feat-router-telemetry-v3-1777866642
- [x] gh pr create --label sisyphus
