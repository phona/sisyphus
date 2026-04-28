# REQ-ruff-fix-dispatch-idempotency-1777342033: Fix ruff lint errors in dispatch idempotency challenger test

## Problem

PR #176 (REQ-427 BKD POST + orch ingest slug deduplication) left two ruff lint
errors in `orchestrator/tests/test_contract_dispatch_idempotency_challenger.py`:

- **I001** — import block is un-sorted or un-formatted (line 31)
- **F841 (×2)** — local variable `result` assigned to but never used (lines 270, 342)

These errors block `make ci-lint` on every subsequent PR that touches the
orchestrator package, including PRs #183, #184, and #179.

## Solution

Fix only `orchestrator/tests/test_contract_dispatch_idempotency_challenger.py`:

1. Re-sort the import block so ruff isort (I001) passes.
2. Drop the `result =` assignment on the two `await invoke_verifier(...)` calls
   whose return values are never read (F841).

No logic is changed; no other file is touched.

## Scope

- Single file: `orchestrator/tests/test_contract_dispatch_idempotency_challenger.py`
- Change size: −3 lines, 0 lines added (net diff < 10 lines)
- No migrations, no schema changes, no API changes
