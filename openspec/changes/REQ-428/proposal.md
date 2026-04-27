# REQ-428: verifier infra-flake auto-retry

## Problem

When a mechanical checker stage (staging_test / dev_cross_check / spec_lint / pr_ci) fails
due to transient infrastructure issues (network blips, docker rate-limit, kubectl exec channel
race, SonarQube 503, GHA runner preemption), the verifier currently has only three options:
pass / fix / escalate. Since the failure is not a code bug, it cannot `fix` or `pass`.
It must `escalate`, which creates a GitHub incident and requires human intervention for what
is often a self-healing transient condition.

The pre-verifier `_flake.py` retry handles known regex-matchable patterns, but:
1. Some infra-flake patterns are only visible in the full log context (SonarQube 503 in a
   multi-step CI job, GHA runner preemption in a workflow step), not via simple regex on
   stderr tail.
2. When `_flake.py` retry budget is exhausted, the verifier must escalate regardless.

## Proposal

Add `retry` as a 4th verifier decision action, allowing the verifier to autonomously
re-run a mechanical checker stage when it judges the failure to be a clear infra-flake.

- Bounded by `verifier_infra_retry_cap` (default: 2 retries)
- Exceeding the cap falls through to `escalate` with reason=`infra-retry-cap`
- Only covers mechanical checker stages: staging_test, dev_cross_check, spec_lint, pr_ci
- Other stages (analyze, accept, challenger) fall back to escalate if retry is attempted

## Impact

- Reduces human escalation for transient infra issues
- No change to existing pass/fix/escalate semantics
- Conservative: verifier must be confident (confidence=high) to use retry
