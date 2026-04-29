# Tasks — REQ-execute-artifact-check-1777254586

> owner: execute-agent or sub-agent of execute
> 所有 checkbox 完成时勾上，反映真实做了的事。

## Stage: contract / spec

- [x] author `proposal.md`（why / what / impact）
- [x] author `design.md`（决策、trade-offs、risks）
- [x] author `specs/execute-artifact-check/spec.md`（delta + scenarios AAC-S1..S8）
- [x] 复用 artifact_checks 表，无 schema 变更

## Stage: implementation — orchestrator

- [x] 加 `ReqState.EXECUTE_ARTIFACT_CHECKING`
- [x] 加 `Event.EXECUTE_ARTIFACT_CHECK_PASS` / `Event.EXECUTE_ARTIFACT_CHECK_FAIL`
- [x] 改 `(EXECUTING, EXECUTE_DONE)` transition：next_state 改为 `EXECUTE_ARTIFACT_CHECKING`，action 改为 `create_execute_artifact_check`
- [x] 加 `(EXECUTE_ARTIFACT_CHECKING, EXECUTE_ARTIFACT_CHECK_PASS)` → `SPEC_LINT_RUNNING` + `create_spec_lint`
- [x] 加 `(EXECUTE_ARTIFACT_CHECKING, EXECUTE_ARTIFACT_CHECK_FAIL)` → `REVIEW_RUNNING` + `invoke_verifier_for_execute_artifact_check_fail`
- [x] 把 `EXECUTE_ARTIFACT_CHECKING` 加进 SESSION_FAILED self-loop 列表
- [x] `engine.STATE_TO_STAGE`：`EXECUTE_ARTIFACT_CHECKING → "execute_artifact_check"`
- [x] `engine._EVENT_TO_OUTCOME`：PASS=pass / FAIL=fail
- [x] `watchdog._STATE_ISSUE_KEY[EXECUTE_ARTIFACT_CHECKING] = None`
- [x] 新文件 `checkers/execute_artifact_check.py` —— mirror spec_lint：`_build_cmd(req_id)` + `run_execute_artifact_check(req_id, *, timeout_sec=120)`，返回 `CheckResult`
- [x] 新文件 `actions/create_execute_artifact_check.py` —— mirror create_spec_lint：跑 checker，写 artifact_checks，emit pass/fail
- [x] `actions/__init__.py` 注册 import
- [x] `actions/_verifier.py`：
  - [x] `_STAGES` 加 `execute_artifact_check`
  - [x] `_PASS_ROUTING["execute_artifact_check"] = (EXECUTE_ARTIFACT_CHECKING, EXECUTE_ARTIFACT_CHECK_PASS)`
  - [x] 新 handler `invoke_verifier_for_execute_artifact_check_fail`
- [x] 新模板 `prompts/verifier/execute_artifact_check_fail.md.j2`
- [x] 新模板 `prompts/verifier/execute_artifact_check_success.md.j2`（fixer recheck 用）

## Stage: tests

- [x] `test_checkers_execute_artifact_check.py`：build_cmd 形状 + pass/fail/timeout/empty-source guards + flake retry
- [x] 扩 `test_actions_smoke.py` or 新文件：create_execute_artifact_check pass/fail/timeout 三路
- [x] `test_state_machine_execute_artifact_check.py`：4 条新 transition + SESSION_FAILED self-loop
- [x] `test_contract_execute_artifact_check.py`：AAC-S1..S8 黑盒 scenarios

## Stage: PR

- [x] git push feat/REQ-execute-artifact-check-1777254586
- [x] gh pr create --label sisyphus
