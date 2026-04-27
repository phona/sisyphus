## ADDED Requirements

### Requirement: verifier can output retry to auto-rerun infra-flake checker stages

The system SHALL accept `"action": "retry"` as a valid verifier decision. When the
verifier outputs `action=retry`, sisyphus MUST automatically re-run the same mechanical
checker stage (staging_test, dev_cross_check, spec_lint, or pr_ci) without requiring
human intervention. The retry MUST be bounded by `verifier_infra_retry_cap` (default 2).
When `ctx.infra_retry_count >= verifier_infra_retry_cap`, the system MUST emit
`verify.escalate` with `reason=infra-retry-cap` instead of retrying.

#### Scenario: VFR-S1 retry decision routes to apply_verify_infra_retry action

- **GIVEN** state is REVIEW_RUNNING
- **WHEN** verifier session completes with decision `{"action": "retry", "fixer": null, "reason": "infra flaky: kubectl exec channel", "confidence": "high"}`
- **THEN** engine dispatches action `apply_verify_infra_retry`
- **AND** next_state remains REVIEW_RUNNING (self-loop, action internally CASes)

#### Scenario: VFR-S2 infra-retry below cap CASes back to stage_running and re-runs create

- **GIVEN** state is REVIEW_RUNNING, ctx.infra_retry_count=0, verifier_stage=staging_test
- **WHEN** apply_verify_infra_retry fires
- **THEN** infra_retry_count increments to 1 in ctx
- **AND** state is CAS-ed to STAGING_TEST_RUNNING
- **AND** create_staging_test action is invoked to re-run the checker

#### Scenario: VFR-S3 infra-retry at cap emits verify.escalate

- **GIVEN** state is REVIEW_RUNNING, ctx.infra_retry_count=2 (= verifier_infra_retry_cap)
- **WHEN** apply_verify_infra_retry fires
- **THEN** system emits verify.escalate with reason=infra-retry-cap
- **AND** state transitions to ESCALATED (via escalate action)

#### Scenario: VFR-S4 retry decision for non-retryable stage escalates

- **GIVEN** state is REVIEW_RUNNING, verifier_stage=analyze (not in _RETRY_ROUTING)
- **WHEN** verifier outputs action=retry
- **THEN** apply_verify_infra_retry emits verify.escalate
- **AND** logs warning stage_not_retryable

#### Scenario: VFR-S5 retry action requires fixer=null

- **GIVEN** a decision JSON with `{"action": "retry", "fixer": "dev", ...}`
- **WHEN** validate_decision is called
- **THEN** validation MUST fail with "action=retry must have null fixer"

#### Scenario: VFR-S6 router maps action=retry to VERIFY_INFRA_RETRY event

- **GIVEN** a valid decision with action=retry and fixer=null
- **WHEN** decision_to_event is called
- **THEN** returns Event.VERIFY_INFRA_RETRY
