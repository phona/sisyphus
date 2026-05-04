# Tasks: REQ-feat-intent-entrypoint-400-1777882563

## Stage: contract / spec

- [x] author specs/intent-entrypoints/spec.md with 5 scenarios (IEP-S1..S5)
- [x] author proposal.md with design rationale

## Stage: implementation

- [x] `orchestrator/src/orchestrator/state.py`: add INTENT_TEST / INTENT_PR_CI / INTENT_ACCEPT / INTENT_ARCHIVE events + 4 INIT transitions
- [x] `orchestrator/src/orchestrator/router.py`: recognize intent:test / intent:pr_ci / intent:accept / intent:archive tags in derive_event
- [x] `orchestrator/src/orchestrator/intent_tags.py`: add extract_pr_tag helper
- [x] `orchestrator/src/orchestrator/actions/create_staging_test.py`: add intent:test precondition validation + workspace setup from pr: tag
- [x] `orchestrator/src/orchestrator/actions/create_pr_ci_watch.py`: add intent:pr_ci precondition validation + ctx injection
- [x] `orchestrator/src/orchestrator/actions/create_accept.py`: add intent:accept precondition validation
- [x] `docs/state-machine.md`: add 4 new event rows + mermaid transitions
- [x] `docs/architecture.md`: update entry-point choices section

## Stage: tests

- [x] `orchestrator/tests/test_state.py`: add IEP-S1..S5 transition assertions
- [x] `orchestrator/tests/test_router.py`: add intent:test / intent:pr_ci / intent:accept / intent:archive routing tests

## Stage: PR（推之前必须全绿）

- [x] git push feat/REQ-feat-intent-entrypoint-400-1777882563
- [x] make ci-lint → 全绿
- [x] make ci-unit-test → 全绿
- [x] make ci-integration-test → 全绿（无 PG 环境自动跳过）
- [x] gh pr create
