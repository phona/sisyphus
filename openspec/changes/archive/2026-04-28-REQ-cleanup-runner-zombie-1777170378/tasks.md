# Tasks: REQ-cleanup-runner-zombie-1777170378

## Stage: spec

- [x] `openspec/changes/REQ-cleanup-runner-zombie-1777170378/proposal.md`
- [x] `openspec/changes/REQ-cleanup-runner-zombie-1777170378/tasks.md`
- [x] `openspec/changes/REQ-cleanup-runner-zombie-1777170378/specs/cleanup-runner-zombie/spec.md`
- [x] `openspec/changes/REQ-cleanup-runner-zombie-1777170378/specs/cleanup-runner-zombie/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/admin.py`：
  - 新增 `_force_escalate_cleanup_tasks: set[asyncio.Task]` 模块级集合
  - `force_escalate` 函数 SQL UPDATE 后 schedule
    `engine._cleanup_runner_on_terminal(req_id, ReqState.ESCALATED)` fire-and-forget
  - `force_escalate` docstring 增一段说明：raw SQL UPDATE 必须显式触发 cleanup，
    runner_gc 不会扫 escalated retention 内的 Pod

## Stage: tests

- [x] `orchestrator/tests/test_admin.py`：
  - `test_force_escalate_marks_escalated_and_triggers_cleanup`：state=analyzing →
    SQL UPDATE 跑一次 + `_cleanup_runner_on_terminal(req_id, ReqState.ESCALATED)`
    被 schedule 一次（asyncio.sleep(0) 让 task 跑）
  - `test_force_escalate_noop_when_already_escalated_no_cleanup`：state=escalated
    → 200 noop，没 SQL UPDATE 没 cleanup task
  - 既存 `test_force_escalate_404_when_not_found` 不变（404 路径不该 schedule cleanup）

## Stage: PR

- [x] git push feat/REQ-cleanup-runner-zombie-1777170378
- [x] gh pr create
