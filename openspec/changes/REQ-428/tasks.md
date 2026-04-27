# REQ-428 Tasks

## Stage: spec
- [x] Write specs/verifier-infra-flake-retry/spec.md with scenarios

## Stage: implementation
- [x] state.py: add VERIFY_INFRA_RETRY event + transition
- [x] router.py: add "retry" to _VALID_ACTIONS; map to VERIFY_INFRA_RETRY; validate fixer=null
- [x] actions/_verifier.py: add _RETRY_ROUTING dict + apply_verify_infra_retry handler
- [x] config.py: add verifier_infra_retry_cap: int = 2
- [x] prompts/verifier/_decision.md.j2: document retry as 4th action
- [x] prompts/verifier/staging_test_fail.md.j2: update infra-flake guidance to retry
- [x] prompts/verifier/dev_cross_check_fail.md.j2: same
- [x] prompts/verifier/pr_ci_fail.md.j2: same
- [x] prompts/verifier/spec_lint_fail.md.j2: add infra-flake retry guidance
- [x] docs/state-machine.md: document new event + update verifier section

## Stage: tests
- [x] test_router.py: validate retry action schema rules
- [x] test_engine_verifier_loop.py: VLT-S17 infra-retry self-loop + VLT-S18 cap escalation

## Stage: PR
- [x] git push feat/REQ-428
- [x] gh pr create with sisyphus label
