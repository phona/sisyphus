# Tasks: REQ-start-actions-ctx-persist-1777345119

## Stage: implementation (start_challenger)

- [x] `orchestrator/src/orchestrator/actions/start_challenger.py`：
  - 新增 `from ..store import db, req_state` import
  - 在 `bkd.update_issue` 调用后写 ctx：`await req_state.update_context(pool, req_id, {"challenger_issue_id": issue.id})`
  - return dict 补 `challenger_issue_id`

## Stage: implementation (watchdog)

- [x] `orchestrator/src/orchestrator/watchdog.py`：
  - `_STATE_ISSUE_KEY` 新增 `ReqState.CHALLENGER_RUNNING: "challenger_issue_id"` 条目（在 DEV_CROSS_CHECK_RUNNING 之后）
  - `_check_and_escalate`：issue_id 解析后、BKD get_issue 调用前，加 CHALLENGER_RUNNING 防守性跳过：
    `if state == ReqState.CHALLENGER_RUNNING and issue_id is None: log.warning(...); return False`

## Stage: tests

- [x] `orchestrator/tests/test_actions_start_challenger.py`：
  - 新增 autouse fixture `_mock_db_and_req_state`：monkeypatch `start_challenger.db.get_pool` + `start_challenger.req_state.update_context`（防止真 DB 调用）
  - 新增 `test_start_challenger_writes_challenger_issue_id_to_ctx`：验证 `req_state.update_context` 被调且 `call_args[2] == {"challenger_issue_id": "ch-new-1"}`
- [x] `orchestrator/tests/test_watchdog.py`：
  - 复原 `test_missing_issue_id_in_ctx_escalates` 为 STAGING_TEST_RUNNING（新守卫不影响该 state）
  - 新增 `test_challenger_running_in_state_issue_key`：验证 `_STATE_ISSUE_KEY[CHALLENGER_RUNNING] == "challenger_issue_id"`
  - 新增 `test_challenger_running_missing_issue_id_skips`：CHALLENGER_RUNNING + ctx={} → `_check_and_escalate` 返 False，engine.step 不被调
  - 新增 `test_challenger_running_session_skips_when_still_running`：CHALLENGER_RUNNING + issue_id in ctx + session_status="running" → 跳过
  - 新增 `test_challenger_running_session_failed_escalates`：CHALLENGER_RUNNING + issue_id in ctx + session_status="failed" → escalate（engine.step 被调）

## Stage: spec

- [x] `openspec/changes/REQ-start-actions-ctx-persist-1777345119/proposal.md`
- [x] `openspec/changes/REQ-start-actions-ctx-persist-1777345119/tasks.md`
- [x] `openspec/changes/REQ-start-actions-ctx-persist-1777345119/specs/start-actions-ctx-persist/contract.spec.yaml`
- [x] `openspec/changes/REQ-start-actions-ctx-persist-1777345119/specs/start-actions-ctx-persist/spec.md`

## Stage: PR

- [x] git push feat/REQ-start-actions-ctx-persist-1777345119
- [x] gh pr create
