# Tasks

## Stage: contract / spec
- [x] author `proposal.md`
- [x] author `specs/orch-webhook-verifier-resume/spec.md` —— ADDED Requirement webhook
      resume bypass + admin retrigger endpoint，含 scenarios VWR-S1..S5

## Stage: implementation
- [x] `webhook.py`：dedup `skip` 分支细化 —— session.completed + verifier tag
      场景下，先 GET issue 取 tags 解析当前 REQ，state == REVIEW_RUNNING 时
      bypass dedup，emit `webhook.dedup.verifier_resume_bypass` 含 executionId
- [x] `webhook.py`：dedup new / retry / skip 三路统一 emit obs event
      `webhook.dedup.observed`，extras 含 event_id + executionId + status
- [x] `admin.py`：新增 `RetriggerVerifierBody` + 路由
      `POST /admin/req/{req_id}/retrigger-verifier`，从 BKD chat 重新读取
      verifier 决策喂 engine.step；失败 reason 明确

## Stage: tests
- [x] `tests/test_webhook_verifier_resume.py`：覆盖 5 个 scenario
      （VWR-S1..S5），全 monkeypatch BKDClient + db / req_state，无 PG 依赖

## Stage: PR（推之前必须全绿）
- [x] git push feat/REQ-fix-webhook-resume-380-v2-1777866642
- [x] `make ci-lint` → 全绿
- [x] `make ci-unit-test` → 全绿
- [x] `make ci-integration-test` → 全绿（无 PG 视为 pass）
- [x] gh pr create --label sisyphus + sisyphus cross-link footer
