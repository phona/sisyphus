# Tasks

## Stage: contract / spec

- [x] author specs/accept-env-observability/spec.md (sub_steps + KEEP_ENV requirements)
- [x] update docs/integration-contracts.md §2.3 (accept-env-down KEEP_ENV contract)
- [x] update docs/integration-contracts.md §3 (sub_steps optional JSON field)
- [x] update docs/cookbook/ttpos-arch-lab-accept-env.md (example sub_steps emission + KEEP_ENV branch)

## Stage: implementation

- [x] add `settings.accept_keep_env` config flag (default False)
- [x] extract sub-step parsing helper in actions/create_accept.py (`_record_sub_steps`)
- [x] insert one stage_runs row per sub_step after env-up parses successfully
- [x] plumb KEEP_ENV=1 into teardown_accept_env.py exec env when flag is on
- [x] structured log warning on malformed sub_steps (best-effort, never raise)

## Stage: unit tests

- [x] test_create_accept_substeps.py: sub_steps array parsed → N stage_runs inserts
- [x] test_create_accept_substeps.py: missing/invalid sub_steps → no inserts, no error
- [x] test_teardown_accept_env_keep_env.py: flag on → KEEP_ENV=1 in exec env
- [x] test_teardown_accept_env_keep_env.py: flag off (default) → KEEP_ENV not in exec env

## Stage: PR (推之前必须全绿)

- [x] git push feat/REQ-feat-accept-env-substep-timing-1777812776
- [x] make ci-lint → 全绿
- [x] make ci-unit-test → 全绿
- [x] make ci-integration-test → 全绿（无 PG 环境自动跳过）
- [x] openspec validate REQ-feat-accept-env-substep-timing-1777812776 → 全绿
- [x] check-scenario-refs.sh → 全绿
- [x] gh pr create --label sisyphus
