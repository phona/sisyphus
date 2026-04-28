# Tasks: REQ-k8s-concurrent-runner-race-1777339786

## Stage: spec

- [x] author specs/k8s-runner-concurrency/spec.md with concurrency safety requirement and scenarios

## Stage: implementation

- [x] Add `self._k8s_api_lock = asyncio.Lock()` to `RunnerController.__init__`
- [x] Add `_k8s(fn, *args, **kwargs)` helper that acquires lock then calls `asyncio.to_thread`
- [x] Replace all 15 `asyncio.to_thread(self.core_v1.<method>, ...)` call sites with `self._k8s(...)`
- [x] Unit tests: add `_FragileCore` simulation class + `test_ensure_runner_concurrent_no_apiexception_status0` concurrent regression test (runs real `_wait_pod_ready` path, no monkeypatch of that method)
- [x] All 39 tests pass; ruff lint clean

## Stage: PR

- [x] Commit changes on feat branch
- [x] `gh pr create --label sisyphus`
