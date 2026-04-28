# Tasks — REQ-runner-gc-pod-pvc-split-1777283946

owner: analyze-agent or sub-agent of analyze

## Stage: spec
- [x] author specs/runner-gc-pod-pvc-split/contract.spec.yaml
- [x] author specs/runner-gc-pod-pvc-split/spec.md scenarios (RGS-S1..S6)

## Stage: implementation
- [x] `k8s_runner.py`: 删 `gc_orphans`，加 `gc_orphan_pods` + `gc_orphan_pvcs`
- [x] `runner_gc.py`: `_active_req_ids` 拆 `_pod_keep_req_ids` + `_pvc_keep_req_ids`
- [x] `runner_gc.py`: `gc_once` 调两个新 controller 方法，返 dict 加 `cleaned_pods` / `cleaned_pvcs`
- [x] `runner_gc.py`: `run_loop` 日志字段调整为 pods/pvcs

## Stage: unit tests
- [x] `tests/test_runner_gc.py`: 改现有 case assert 两个 keep set
- [x] `tests/test_runner_gc.py`: 加 `test_pod_keep_excludes_escalated_within_retention`
- [x] `tests/test_k8s_runner.py`: 替 `test_gc_orphans_*` 成 `test_gc_orphan_pods_*` + `test_gc_orphan_pvcs_*`
- [x] `tests/test_contract_orch_noise_cleanup.py`: `_FakeController` 替 `gc_orphans` 成 `gc_orphan_pods` + `gc_orphan_pvcs`

## Stage: PR
- [x] `make ci-lint` + `make ci-unit-test` 在 runner pod 内通过
- [x] git push feat/REQ-runner-gc-pod-pvc-split-1777283946
- [x] gh pr create --label sisyphus
