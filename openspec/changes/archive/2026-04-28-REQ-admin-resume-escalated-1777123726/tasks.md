# Tasks: REQ-admin-resume-escalated-1777123726

## Stage: spec

- [x] `openspec/changes/REQ-admin-resume-escalated-1777123726/proposal.md`
- [x] `openspec/changes/REQ-admin-resume-escalated-1777123726/design.md`
- [x] `openspec/changes/REQ-admin-resume-escalated-1777123726/tasks.md`
- [x] `openspec/changes/REQ-admin-resume-escalated-1777123726/specs/admin-resume-endpoint/spec.md`
- [x] `openspec/changes/REQ-admin-resume-escalated-1777123726/specs/admin-resume-endpoint/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/admin.py`：
  - 重命名 `@admin.post("/req/{req_id}/pause")` → `/req/{req_id}/runner-pause`（函数名 `pause_runner` 不变）
  - 重命名 `@admin.post("/req/{req_id}/resume")` → `/req/{req_id}/runner-resume`（函数名 `resume_runner` 不变）
  - 加 `class ResumeBody(BaseModel)`：action / stage / fixer / reason 四字段
  - 加 `@admin.post("/req/{req_id}/resume")` async def `resume_req`（state-level）
  - 模块 docstring 更新：runner ops 段重命名 + 新加 state-level resume 一行

- [x] `orchestrator/docs/V0.2-PLAN.md`：admin 工具表把 pause/resume 改成 runner-pause/runner-resume

- [x] `orchestrator/docs/sisyphus-integration.md`：`POST /admin/req/REQ-N/pause` 引用改成 `runner-pause`

## Stage: tests

- [x] `orchestrator/tests/test_admin.py`，新增 case：

  - `test_resume_404_when_not_found`：req_state.get 返 None → 404
  - `test_resume_409_when_not_escalated`：state=analyzing → 409 提示 hint
  - `test_resume_400_when_pass_missing_stage`：action=pass + ctx 没 verifier_stage + body 没 stage → 400
  - `test_resume_pass_dispatches_verify_pass_event`：state=escalated, ctx.verifier_stage=staging_test, action=pass → engine.step 被调一次 with event=VERIFY_PASS
  - `test_resume_pass_with_body_stage_overrides_ctx`：body.stage="pr_ci" → ctx 被 patch + engine.step 收到正确 event
  - `test_resume_fix_needed_dispatches_verify_fix_needed_event`：action=fix-needed → engine.step with event=VERIFY_FIX_NEEDED
  - `test_resume_writes_audit_to_context`：body.reason="rerun infra flake" → ctx.resume_reason / resumed_by_admin 被 patch
  - `test_resume_invalid_action_422`：body.action="bogus" → pydantic ValidationError
  - `test_resume_auth_check_before_db`：bad token → 401，没 req_state.get
  - `test_runner_pause_path_renamed`：函数 `pause_runner` 装饰器路径是 `/req/{req_id}/runner-pause`
  - `test_runner_resume_path_renamed`：函数 `resume_runner` 装饰器路径是 `/req/{req_id}/runner-resume`

## Stage: PR

- [x] git push feat/REQ-admin-resume-escalated-1777123726
- [x] gh pr create
