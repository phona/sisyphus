# Tasks for REQ-447

## Stage: spec

- [x] author specs/ttpos-ci-direct-dispatch/spec.md scenarios
- [x] author specs/ttpos-ci-direct-dispatch/contract.spec.yaml

## Stage: implementation

- [x] config.py: add ci_dispatch_enabled / ci_dispatch_repo / ci_dispatch_event_type
- [x] create_pr_ci_watch.py: add _dispatch_to_ci_repo helper + wire into _run_checker
- [x] unit tests: test_actions_create_pr_ci_watch.py (dispatch scenarios)

## Stage: docs

- [x] docs/integration-contracts.md: §6 footnote on direct dispatch contract

## Stage: PR

- [x] git push feat/REQ-447
- [x] gh pr create
