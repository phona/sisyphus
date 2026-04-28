## MODIFIED Requirements

### Requirement: dispatch idempotency challenger test file carries no ruff lint findings

The file `orchestrator/tests/test_contract_dispatch_idempotency_challenger.py` MUST be free of all ruff lint findings under the project's existing `pyproject.toml` rule selection. Specifically, the import block MUST be
sorted and formatted in conformance with ruff's isort rules (I001 clean),
and no local variable SHALL be assigned a value that is never subsequently
read within the same scope (F841 clean). These constraints SHALL be verified
by running `uv run ruff check tests/test_contract_dispatch_idempotency_challenger.py`
inside the `orchestrator/` directory and confirming exit code 0 with
"All checks passed!" output.

#### Scenario: RUFF-S1 ruff check on the challenger test file exits 0

- **GIVEN** a checkout of the repository at the merge of this REQ
- **WHEN** an operator runs `cd orchestrator && uv run ruff check tests/test_contract_dispatch_idempotency_challenger.py`
- **THEN** the command exits 0 and prints `All checks passed!` with zero I001 or F841 diagnostics

#### Scenario: RUFF-S2 import block is sorted in the challenger test file

- **GIVEN** a checkout of the repository at the merge of this REQ
- **WHEN** an operator reads the import section of `test_contract_dispatch_idempotency_challenger.py`
- **THEN** `from __future__ import annotations` is followed immediately by a blank line and then `from unittest.mock import ...` with no additional blank lines between the two import groups

#### Scenario: RUFF-S3 no unused local variables in test_DISP_S2

- **GIVEN** a checkout of the repository at the merge of this REQ
- **WHEN** an operator reads `test_DISP_S2_no_slug_hit_calls_create_issue_and_stores_slug`
- **THEN** the `await invoke_verifier(...)` call within the `with patches[...]` block is a bare expression statement with no left-hand-side assignment

#### Scenario: RUFF-S4 no unused local variables in test_DISP_S5

- **GIVEN** a checkout of the repository at the merge of this REQ
- **WHEN** an operator reads `test_DISP_S5_round_aware_slug_distinguishes_fixer_rounds`
- **THEN** the `await invoke_verifier(...)` call within the `with patches[...]` block is a bare expression statement with no left-hand-side assignment
