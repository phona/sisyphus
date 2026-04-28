# Tasks — REQ-ruff-fix-dispatch-idempotency-1777342033

## Stage: contract / spec

- [x] author specs/ruff-lint-fix/spec.md with MODIFIED requirement and scenarios
- [x] author specs/ruff-lint-fix/contract.spec.yaml (no API surface — code quality only)

## Stage: implementation

- [x] Fix I001: re-sort import block in test_contract_dispatch_idempotency_challenger.py
- [x] Fix F841 (×2): drop unused `result =` assignments on two `await invoke_verifier(...)` calls
- [x] Verify `uv run ruff check tests/test_contract_dispatch_idempotency_challenger.py` → All checks passed!
- [x] Run ci-unit-test (1643 tests pass, no regressions)

## Stage: PR

- [x] git push feat/REQ-ruff-fix-dispatch-idempotency-1777342033
- [x] gh pr create --label sisyphus (PR opened with sisyphus cross-link footer)
