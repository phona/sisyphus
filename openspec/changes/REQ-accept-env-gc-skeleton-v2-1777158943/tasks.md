# REQ-accept-env-gc-skeleton-v2-1777158943 — tasks

## Stage: spec

- [x] author proposal.md（动机：minimal REQ 没合 main，骨架切片重试；故意不做范围）
- [x] author specs/accept-env-gc-skeleton/spec.md（2 条 Requirement，
      ADDED delta，每条带 SHALL/MUST prose + Scenario）
- [x] author specs/accept-env-gc-skeleton/contract.spec.yaml（API 表面契约：
      模块路径、2 个函数签名 + async 标记）

## Stage: implementation

- [x] orchestrator/src/orchestrator/accept_env_gc.py：模块 docstring +
      `async def gc_once()` + `async def run_loop()`，两者抛
      `NotImplementedError("accept_env_gc skeleton only ...")`，零外部 import
      （除 `__future__ annotations`）

## Stage: test

- [x] orchestrator/tests/test_accept_env_gc_skeleton.py：
  - AEGS-S1 `accept_env_gc` 模块可被 import 且 `gc_once` / `run_loop`
    都是 `asyncio.iscoroutinefunction`-True
  - AEGS-S2 `await accept_env_gc.gc_once()` 抛 `NotImplementedError`
  - AEGS-S3 `await accept_env_gc.run_loop()` 抛 `NotImplementedError`

## Stage: PR

- [x] git push feat/REQ-accept-env-gc-skeleton-v2-1777158943
- [x] gh pr create
- [x] move BKD issue to review
