# Tasks — REQ-analyze-artifact-check-1777254586

> owner: analyze-agent or sub-agent of analyze
> 所有 checkbox 完成时勾上，反映真实做了的事。

## Stage: contract / spec

- [x] author `proposal.md`（why / what / impact）
- [x] author `design.md`（决策、trade-offs、risks）
- [x] author `specs/analyze-artifact-check/spec.md`（delta + scenarios AAC-S1..S8）
- [x] 复用 artifact_checks 表，无 schema 变更

## Stage: implementation — orchestrator

- [x] 加 `ReqState.ANALYZE_ARTIFACT_CHECKING`
- [x] 加 `Event.ANALYZE_ARTIFACT_CHECK_PASS` / `Event.ANALYZE_ARTIFACT_CHECK_FAIL`
- [x] 改 `(ANALYZING, ANALYZE_DONE)` transition：next_state 改为 `ANALYZE_ARTIFACT_CHECKING`，action 改为 `create_analyze_artifact_check`
- [x] 加 `(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS)` → `SPEC_LINT_RUNNING` + `create_spec_lint`
- [x] 加 `(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_FAIL)` → `REVIEW_RUNNING` + `invoke_verifier_for_analyze_artifact_check_fail`
- [x] 把 `ANALYZE_ARTIFACT_CHECKING` 加进 SESSION_FAILED self-loop 列表
- [x] `engine.STATE_TO_STAGE`：`ANALYZE_ARTIFACT_CHECKING → "analyze_artifact_check"`
- [x] `engine._EVENT_TO_OUTCOME`：PASS=pass / FAIL=fail
- [x] `watchdog._STATE_ISSUE_KEY[ANALYZE_ARTIFACT_CHECKING] = None`
- [x] 新文件 `checkers/analyze_artifact_check.py` —— mirror spec_lint：`_build_cmd(req_id)` + `run_analyze_artifact_check(req_id, *, timeout_sec=120)`，返回 `CheckResult`
- [x] 新文件 `actions/create_analyze_artifact_check.py` —— mirror create_spec_lint：跑 checker，写 artifact_checks，emit pass/fail
- [x] `actions/__init__.py` 注册 import
- [x] `actions/_verifier.py`：
  - [x] `_STAGES` 加 `analyze_artifact_check`
  - [x] `_PASS_ROUTING["analyze_artifact_check"] = (ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS)`
  - [x] 新 handler `invoke_verifier_for_analyze_artifact_check_fail`
- [x] 新模板 `prompts/verifier/analyze_artifact_check_fail.md.j2`
- [x] 新模板 `prompts/verifier/analyze_artifact_check_success.md.j2`（fixer recheck 用）

## Stage: tests

- [x] `test_checkers_analyze_artifact_check.py`：build_cmd 形状 + pass/fail/timeout/empty-source guards + flake retry
- [x] 扩 `test_actions_smoke.py` or 新文件：create_analyze_artifact_check pass/fail/timeout 三路
- [x] `test_state_machine_analyze_artifact_check.py`：4 条新 transition + SESSION_FAILED self-loop
- [x] `test_contract_analyze_artifact_check.py`：AAC-S1..S8 黑盒 scenarios

## Stage: PR

- [x] git push feat/REQ-analyze-artifact-check-1777254586
- [x] gh pr create --label sisyphus
