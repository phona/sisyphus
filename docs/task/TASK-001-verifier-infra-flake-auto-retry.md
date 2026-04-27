# TASK-001: verifier infra-flake auto-retry

status: in_progress
owner: claude
plan: PLAN-001-verifier-infra-flake-auto-retry.md

## Description

Add `retry` as a 4th verifier decision path for infrastructure-flake failures.
Currently verifier can only pass/fix/escalate; infra-flake forces escalation to human.
This feature lets verifier judge "this is an infra flake, retry the stage automatically"
with a bounded counter before falling back to real escalation.

## Acceptance

- `action=retry` in decision JSON routes to bounded auto-retry of the failed stage
- `verifier_infra_retry_cap` setting controls max retries (default 2)
- Exceeding cap falls through to `escalate` with `reason=infra-retry-cap`
- Stages not covered by infra-retry dispatch escalate instead
- Verifier fail prompts updated to guide `retry` for infra-flake scenarios
