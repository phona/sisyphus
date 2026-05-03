# Tasks

## Stage: spec
- [x] author specs/readyz-namespaced/spec.md scenarios

## Stage: implementation
- [x] orchestrator/src/orchestrator/main.py: `/readyz` K8s 探活切到 `list_namespaced_pod(controller.namespace, limit=1, _request_timeout=2)`
- [x] orchestrator/tests/test_healthz.py: 三处 mock 切到 `list_namespaced_pod`
- [x] orchestrator/tests/test_healthz.py: 新增 `test_readyz_k8s_uses_namespaced_pod_list` 正向用例（assert 调用参数 + assert 没碰 list_namespace）

## Stage: PR
- [x] make ci-lint 全绿（变更文件 ruff 通过）
- [x] make ci-unit-test 全绿（2127 passed）
- [x] git push origin feat/REQ-fix-readyz-namespaced-1777808455
- [x] gh pr create --label sisyphus 含 sisyphus footer
