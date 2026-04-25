# Tasks: REQ-verifier-stagerun-close-1777105576

## Stage: implementation

- [x] `orchestrator/src/orchestrator/engine.py:_record_stage_transitions`：在 `cur_state == next_state` early-return 之前，新增分支：当 `cur_state == ReqState.REVIEW_RUNNING and event == Event.VERIFY_PASS` → `await stage_runs.close_latest_stage_run(pool, req_id, "verifier", outcome="pass")`，包在 try/except 里 best-effort

## Stage: tests

- [x] `orchestrator/tests/test_engine.py`：扩 `FakePool` 让它能记录 `INSERT INTO stage_runs` / `UPDATE stage_runs` 调用（之前是 NotImplementedError 被 try/except 吃掉）
- [x] `orchestrator/tests/test_engine.py:test_verify_pass_closes_orphan_verifier_stage_run` —— REVIEW_RUNNING + VERIFY_PASS → 验 stage_runs UPDATE 被调，stage='verifier'，outcome='pass'
- [x] `orchestrator/tests/test_engine.py:test_verify_fix_needed_still_closes_verifier_via_normal_path` —— 回归测：REVIEW_RUNNING + VERIFY_FIX_NEEDED → FIXER_RUNNING 路径仍走原 close+open 逻辑
- [x] `orchestrator/tests/test_engine.py:test_review_running_self_loop_other_event_does_not_close` —— 只在 `event == VERIFY_PASS` 时 close，其他 self-loop 事件（如 SESSION_FAILED）不动 stage_run

## Stage: spec

- [x] `openspec/changes/REQ-verifier-stagerun-close-1777105576/proposal.md`
- [x] `openspec/changes/REQ-verifier-stagerun-close-1777105576/tasks.md`
- [x] `openspec/changes/REQ-verifier-stagerun-close-1777105576/specs/verifier-stagerun-close/contract.spec.yaml`
- [x] `openspec/changes/REQ-verifier-stagerun-close-1777105576/specs/verifier-stagerun-close/spec.md`

## Stage: PR

- [x] git push feat/REQ-verifier-stagerun-close-1777105576
- [x] gh pr create
