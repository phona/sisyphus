# Tasks: REQ-accept-env-gc-minimal-1777138424

## Stage: spec
- [x] author specs/accept-env-gc/spec.md with ADDED Requirements and Scenarios
- [x] author specs/accept-env-gc/contract.spec.yaml

## Stage: implementation
- [x] implement orchestrator/src/orchestrator/accept_env_gc.py (gc_once + run_loop)
- [x] add accept_env_gc_interval_sec to config.py
- [x] wire accept_env_gc.run_loop() into main.py startup

## Stage: tests
- [x] unit tests in orchestrator/tests/test_accept_env_gc.py (no K8s required)

## Stage: PR
- [x] git push feat/REQ-accept-env-gc-minimal-1777138424
- [x] gh pr create
