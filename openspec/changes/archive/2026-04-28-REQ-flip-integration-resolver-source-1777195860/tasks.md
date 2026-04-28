# Tasks

## Stage: contract / spec

- [x] write delta `specs/self-accept-stage/spec.md` modifying SDA-S4/S5/S7
      + adding SDA-S10 (multi-source + integration tiebreaker)

## Stage: implementation

- [x] flip `_integration_resolver._decide` to source-first
      (`orchestrator/src/orchestrator/actions/_integration_resolver.py`)
- [x] refresh module docstring + `_SCAN_SCRIPT` comment to match new policy
- [x] update unit tests in
      `orchestrator/tests/test_create_accept_self_host.py`:
      - `TestDecide.test_integration_priority_when_present` →
        `test_source_priority_when_single_source_present`
      - `test_resolve_returns_integration_when_present` →
        `test_resolve_returns_source_when_single_source_present`
      - add `test_integration_breaks_tie_for_multiple_sources`

## Stage: PR

- [x] git push feat/REQ-flip-integration-resolver-source-1777195860
- [x] gh pr create
