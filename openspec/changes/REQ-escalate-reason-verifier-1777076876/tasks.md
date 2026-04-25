# Tasks: REQ-escalate-reason-verifier-1777076876

## Stage: spec
- [x] author specs/escalate-reason/spec.md scenarios
- [x] author specs/escalate-reason/contract.spec.yaml

## Stage: implementation
- [x] webhook.py §5.8: set ctx.escalated_reason="verifier-decision" when event==VERIFY_ESCALATE
- [x] escalate.py _is_transient: update explicit check from "verifier-decision-escalate" to "verifier-decision"
- [x] test_actions_smoke.py: update test_escalate_non_transient_immediate to use "verifier-decision"

## Stage: PR
- [x] git push feat/REQ-escalate-reason-verifier-1777076876
- [x] gh pr create
