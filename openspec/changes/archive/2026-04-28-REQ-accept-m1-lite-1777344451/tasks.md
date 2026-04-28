# REQ-accept-m1-lite-1777344451 Tasks

## Stage: spec
- [x] Write specs/accept-stage-lite/spec.md with AML-S1..S6 scenarios

## Stage: implementation
- [x] actions/create_accept.py: rewrite v0.3-lite (per-repo script, no BKD agent)
  - [x] iterate /workspace/source/*/ via bash script
  - [x] Phase 1: make accept-env-up per repo (skip+warn if no target)
  - [x] Phase 2: sleep accept_smoke_delay_sec
  - [x] Phase 3: make accept-smoke per repo (skip+warn if no target)
  - [x] Phase 4: make accept-env-down best-effort per repo
  - [x] store accept_result + accept_fail_repos in ctx before emitting
  - [x] emit accept.pass or accept.fail
- [x] actions/teardown_accept_env.py: add ctx.accept_result fallback
  - [x] read ctx.accept_result first; fall back to result:pass/fail tags
- [x] config.py: add accept_smoke_delay_sec: int = 30

## Stage: tests
- [x] test_create_accept_minimal.py: 4 unit tests (AML-S1..S4)
- [x] test_actions_smoke.py: update 3 stale create_accept smoke tests
- [x] test_create_accept_self_host.py: remove 3 obsolete v0.2 BKD-dispatch tests

## Stage: PR
- [x] git push feat/REQ-accept-m1-lite-1777344451
- [x] gh pr create with sisyphus label
