# Tasks: REQ-admin-complete-endpoint-1777117709

## Stage: spec

- [x] `openspec/changes/REQ-admin-complete-endpoint-1777117709/proposal.md`
- [x] `openspec/changes/REQ-admin-complete-endpoint-1777117709/tasks.md`
- [x] `openspec/changes/REQ-admin-complete-endpoint-1777117709/specs/admin-complete-endpoint/spec.md`
- [x] `openspec/changes/REQ-admin-complete-endpoint-1777117709/specs/admin-complete-endpoint/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/admin.py`：
  - 加 `CompleteBody(BaseModel)` 含 optional `reason: str | None = None`
  - 加 `@admin.post("/req/{req_id}/complete")` async def `complete_req`
  - import `_cleanup_runner_on_terminal` from engine
  - 模块 docstring 列表加 `POST /req/{req_id}/complete`

- [x] `orchestrator/src/orchestrator/engine.py`：
  - 不改名，但加一行注释说明 `_cleanup_runner_on_terminal` 被 admin.py 复用
  - （非必需，确认 export 可达即可）

## Stage: tests

- [x] `orchestrator/tests/test_admin.py`：
  - `test_complete_404_when_not_found`：req_state.get 返回 None → 404
  - `test_complete_noop_when_already_done`：state=done → 200 noop
  - `test_complete_409_when_not_escalated`：state=analyzing → 409
  - `test_complete_marks_done_and_triggers_cleanup`：state=escalated →
    SQL UPDATE 被调一次 + `_cleanup_runner_on_terminal` 被 schedule 一次
  - `test_complete_writes_reason_in_context`：body.reason="x" 时 ctx patch 含 reason

## Stage: PR

- [x] git push feat/REQ-admin-complete-endpoint-1777117709
- [x] gh pr create
