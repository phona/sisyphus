# Tasks — REQ-test-router-decision-contract-1777860546

## Stage: contract / spec
- [x] author `openspec/changes/.../specs/router-decision-contract-tests/spec.md`
- [x] verify `openspec validate REQ-test-router-decision-contract-1777860546`
- [x] verify `check-scenario-refs.sh` clean

## Stage: implementation
- [x] add `orchestrator/tests/router/test_verifier_decision_parsing.py`
- [x] cover parametrised table (12 rows: 4 happy, 5 escalate, 1 description-only, 2 unknown-stage)
- [x] cover 3 focused invariants (no-stage escalate, None/None defensive, base64 precedence)
- [x] no production code change (`router.py`, `verifier_parser.py`, `state.py` untouched)

## Stage: PR
- [x] `make ci-lint` green
- [x] new tests pass standalone (`pytest tests/router/test_verifier_decision_parsing.py -v`)
- [x] tests near the change (`tests/router/`, `test_router.py`, `test_verifier.py`,
      `test_router_validate_audit_schema.py`, `test_webhook_verifier_retry.py`) all green
- [x] orchestrator-wide unit-test failures are pre-existing (tracked in phona/sisyphus#364
      — test isolation pollution, unrelated to this REQ's diff)
- [x] push `feat/REQ-test-router-decision-contract-1777860546`
- [x] open PR with `sisyphus` label and cross-link footer
