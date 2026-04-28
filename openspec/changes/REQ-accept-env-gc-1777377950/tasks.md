## Stage: spec
- [x] author proposal.md
- [x] author specs/accept-env-gc/spec.md scenarios

## Stage: implementation
- [x] implement accept_env_gc.py (gc_once / run_loop / get_last_result)
- [x] add RunnerController.list_accept_env_namespaces + delete_namespace
- [x] add config accept_env_gc_interval_sec
- [x] wire accept_env_gc.run_loop() into main.py startup
- [x] add admin endpoints POST /admin/accept-env-gc + GET /admin/accept-env-gc/status
- [x] replace skeleton tests with real unit tests (9 cases)

## Stage: PR
- [x] git push feat/REQ-accept-env-gc-1777377950
- [x] gh pr create --label sisyphus
