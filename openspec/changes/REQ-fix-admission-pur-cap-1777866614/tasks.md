# Tasks: REQ-fix-admission-pur-cap-1777866614

## Stage: contract / spec

- [x] author `specs/orch-rate-limit/spec.md` MODIFIED Requirements 把
      `pending-user-review` 加进排除状态列表
- [x] add scenario `ORCH-RATE-S7 pending-user-review excluded from in-flight cap`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/admission.py`:
      `_INFLIGHT_EXCLUDE_STATES` 末尾加 `"pending-user-review"`，更新注释
      解释 PUR 跟 escalated 同档（runner pod 已拆）

## Stage: tests

- [x] `orchestrator/tests/test_admission.py::test_inflight_count_under_cap_admits`
      —— SQL state-list 断言追加 `pending-user-review`
- [x] `orchestrator/tests/test_admission.py::test_inflight_excludes_pending_user_review`
      —— 新 case ORCH-RATE-S7：`_FakePool(count=2)` 模拟 8 PUR 已排除，admit=True

## Stage: PR

- [x] `git push origin feat/REQ-fix-admission-pur-cap-1777866614`
- [x] `gh pr create` with proposal summary + verification plan
