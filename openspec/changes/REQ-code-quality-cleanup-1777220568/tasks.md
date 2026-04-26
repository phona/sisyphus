# Tasks — REQ-code-quality-cleanup-1777220568

## Stage: contract / spec
- [x] author `specs/code-quality/spec.md` capturing the two hygiene rules
      (no dead parameters, no vulture-100 %-confidence findings in `src/`)
- [x] proposal.md explains why each finding is real vs. false-positive

## Stage: implementation
- [x] `orchestrator/src/orchestrator/router.py`: drop unused
      `result_tags_only` parameter from `derive_event`
- [x] `orchestrator/src/orchestrator/bkd_mcp.py`: rename
      `__aexit__(self, *exc)` → `__aexit__(self, *_exc)`
- [x] `orchestrator/src/orchestrator/bkd_rest.py`: rename
      `__aexit__(self, *exc)` → `__aexit__(self, *_exc)`

## Stage: verification
- [x] `make ci-lint` (`uv run ruff check src/ tests/`) — clean
- [x] `vulture src/ --min-confidence 100` — empty (no findings)
- [x] `make ci-unit-test` (`uv run pytest -m "not integration"`) —
      925 passed

## Stage: PR
- [x] git push `feat/REQ-code-quality-cleanup-1777220568`
- [x] `gh pr create` with motivation + test plan
