# REQ-430 Tasks

## Stage: contract / spec
- [x] author specs/runner-gc-admin/contract.spec.yaml
- [x] author specs/runner-gc-admin/spec.md (scenarios RGA-S1..S5)

## Stage: implementation
- [x] runner_gc.py: add _last_gc_result + get_last_result()
- [x] admin.py: POST /admin/runner-gc
- [x] admin.py: GET /admin/runner-gc/status
- [x] unit tests: test_runner_gc.py — _last_gc_result tracking
- [x] unit tests: test_admin_runner_gc.py — new file

## Stage: PR
- [x] git push feat/REQ-430
- [x] gh pr create
