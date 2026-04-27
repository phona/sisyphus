# PLAN-001: verifier infra-flake auto-retry

status: proposed
task: TASK-001-verifier-infra-flake-auto-retry.md

## Context

Pre-verifier `_flake.py` retries known infra-flake patterns (DNS/kubectl-channel/
github-fetch/registry-rate-limit) up to `checker_infra_flake_retry_max` times before
passing the failure to the verifier. When retries are exhausted or the pattern is not
recognized, the verifier sees the failure.

Currently verifier has 3 paths only: `pass / fix / escalate`. Prompts tell it to
`escalate` for any infra-flake — resulting in ESCALATED state and a human wake-up
even for transient, self-healing infrastructure issues.

## Proposal

Add `retry` as a 4th verifier decision action. When verifier identifies an
infra-flake it outputs `action=retry` instead of `escalate`. The engine checks a
bounded counter (`infra_retry_count` in ctx) and re-runs the stage checker
automatically; if the cap is hit it falls through to real escalation.

## Files to Change

1. `orchestrator/src/orchestrator/state.py`
   - Add `VERIFY_INFRA_RETRY = "verify.infra-retry"` to `Event`
   - Add transition `(REVIEW_RUNNING, VERIFY_INFRA_RETRY) → REVIEW_RUNNING, "apply_verify_infra_retry"`

2. `orchestrator/src/orchestrator/router.py`
   - Add `"retry"` to `_VALID_ACTIONS`
   - Add `if action == "retry": return Event.VERIFY_INFRA_RETRY` in `decision_to_event`
   - Add fixer=null constraint for `retry` in `validate_decision`

3. `orchestrator/src/orchestrator/actions/_verifier.py`
   - Add `_RETRY_ROUTING` dict: stage → (target_ReqState, create_action_name)
   - Add `apply_verify_infra_retry` handler:
     - Read `infra_retry_count` from ctx (default 0)
     - If >= `settings.verifier_infra_retry_cap` → emit VERIFY_ESCALATE (reason=infra-retry-cap)
     - Else: increment count in ctx, CAS REVIEW_RUNNING → target_state, close verifier stage_run,
       call `REGISTRY[create_action_name]` with same (body, req_id, tags, ctx), return result

4. `orchestrator/src/orchestrator/config.py`
   - Add `verifier_infra_retry_cap: int = 2`

5. `orchestrator/src/orchestrator/prompts/verifier/_decision.md.j2`
   - Add `retry` as 4th action in schema
   - Document: only for clear infra-flake (network/registry/kubectl transient), fixer=null,
     must not be used for ambiguous failures

6. `orchestrator/src/orchestrator/prompts/verifier/staging_test_fail.md.j2`
   - Change "docker rate-limit / network flaky / kubectl race → escalate" to "→ retry"

7. `orchestrator/src/orchestrator/prompts/verifier/dev_cross_check_fail.md.j2`
   - Same: infra-flake → retry

8. `orchestrator/src/orchestrator/prompts/verifier/pr_ci_fail.md.j2`
   - Change "明显 flaky（网络、docker rate limit、SonarQube 503）→ escalate" to "→ retry"

9. `orchestrator/src/orchestrator/prompts/verifier/spec_lint_fail.md.j2`
   - Add guidance: kubectl-exec infra flake → retry

10. `docs/state-machine.md`
    - Document new `verify.infra-retry` event and transition

## Risks

- **False-positive retry**: verifier misidentifies a real failure as infra-flake →
  wasted retry budget. Mitigated by: cap (2 retries), requiring `confidence=high` for
  retry, and bounded fallback to escalate.
- **Emit chain from create action**: `create_staging_test` in checker mode returns
  `{"emit": "staging-test.pass/fail"}` which the engine processes from the new
  `STAGING_TEST_RUNNING` state — this is correct and safe.
- **Body reuse**: `apply_verify_infra_retry` passes the verifier webhook body to
  `create_staging_test`; `body.projectId` is the same REQ's project so this is safe.
- **Stage not in retry dispatch**: Non-checker stages (analyze/accept/challenger/
  analyze_artifact_check) are not in `_RETRY_ROUTING`; if verifier outputs `retry`
  for them it falls through to escalate with a warning log.

## Alternatives

- **Escalate-with-special-reason**: Detect `reason="infra-flake:*"` in the escalate
  action and auto-retry there instead of adding a new decision action. Rejected:
  conflates verifier judgment with escalate action behavior; less visible in metrics.
- **Expand pre-verifier flake patterns**: Add more patterns to `_flake.py` to catch
  what verifier currently catches. Rejected: some flakes require semantic understanding
  (e.g., "SonarQube 503 in CI log" is obvious infra-flake but hard to pattern-match
  without reading the full context).
