# Tasks for REQ-escalate-null-reason-audit-1777344785

## Stage: contract / spec

- [x] author `proposal.md` — 根因分析 + 双保险修复策略
- [x] author `specs/escalate-null-reason-fix/spec.md` ADDED delta：
      NRA-S1, NRA-S3..NRA-S8 全部 GIVEN/WHEN/THEN

## Stage: implementation

- [x] `start_analyze.py`: clone 失败路径在 emit VERIFY_ESCALATE 前写 `escalated_reason="clone-failed"`
- [x] `start_analyze_with_finalized_intent.py`: missing finalized intent 路径写 `"missing-finalized-intent"`；clone 失败路径写 `"clone-failed"`
- [x] `create_pr_ci_watch.py`: ValueError config error 路径写 `"pr-ci-timeout"`；exit_code=124 路径写 `"pr-ci-timeout"`
- [x] `create_accept.py`: 5 个 ACCEPT_ENV_UP_FAIL 路径（no integration dir / exec crash / non-zero exit / bad JSON / missing endpoint）全部写 `"accept-env-up-failed"`
- [x] `escalate.py`: 加 `if not final_reason` → default `"unknown"` + warning log；提前落 `update_context(escalated_reason=final_reason)` 在 GH incident 代码之前
- [x] 新增 8 条 NRA-S1..NRA-S8 回归测试（`tests/test_contract_escalate_reason.py`）

## Stage: PR

- [x] git push feat/REQ-escalate-null-reason-audit-1777344785
- [x] gh pr create --label sisyphus
