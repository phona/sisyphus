# REQ-router-session-completed-audit-1777344435 Tasks

## Stage: spec
- [x] Write specs/router-session-completed-audit/spec.md with RSCA-S1 through RSCA-S7 scenarios

## Stage: implementation
- [x] webhook.py: replace step 5.8 single VERIFY_ESCALATE guard with table-driven
      _SESSION_COMPLETED_ESCALATE_REASONS covering VERIFY_ESCALATE, INTAKE_FAIL, PR_CI_TIMEOUT

## Stage: tests
- [x] test_router.py: add 5 missing CASES entries:
      - challenger without result → None
      - fixer without extra tags → FIXER_DONE
      - no stage tag in session.completed → None
      - challenger + result:weird → None
      - staging-test + result:weird → None

## Stage: PR
- [x] git push feat/REQ-router-session-completed-audit-1777344435
- [x] gh pr create with sisyphus label
