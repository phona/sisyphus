# tasks: REQ-runner-cache-on-pvc-1777198512

## Stage: contract / spec

- [x] author `specs/runner-cache-on-pvc/spec.md` with delta `## ADDED Requirements`
- [x] write 4 scenarios `RUNNER-CACHE-S{1..4}` covering each toolchain env var
      (`GOMODCACHE`, `GOCACHE`, `npm_config_cache`, `UV_CACHE_DIR`)

## Stage: implementation

- [x] `orchestrator/src/orchestrator/k8s_runner.py`: extend `build_pod` env list
      with the four cache env vars pointing under `/workspace/.cache/`
- [x] `orchestrator/tests/test_k8s_runner.py`: new
      `test_build_pod_redirects_toolchain_caches_to_pvc` locking exact env names
      and paths

## Stage: PR

- [x] git push `feat/REQ-runner-cache-on-pvc-1777198512`
- [x] gh pr create
