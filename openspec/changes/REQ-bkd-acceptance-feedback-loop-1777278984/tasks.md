# Tasks: REQ-bkd-acceptance-feedback-loop-1777278984

## Stage: contract / spec

- [x] `openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/proposal.md`
- [x] `openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/design.md`
- [x] `openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/specs/user-acceptance-gate/contract.spec.yaml`
- [x] `openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/specs/user-acceptance-gate/spec.md`
- [x] `openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/tasks.md`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/state.py`：
  - 新枚举值 `ReqState.PENDING_USER_REVIEW`
  - 新事件 `Event.USER_REVIEW_PASS` / `Event.USER_REVIEW_FIX`
  - 改 `(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS)` next_state 到 `PENDING_USER_REVIEW`，action 到 `post_acceptance_report`
  - 加 `(PENDING_USER_REVIEW, USER_REVIEW_PASS) → ARCHIVING` action `done_archive`
  - 加 `(PENDING_USER_REVIEW, USER_REVIEW_FIX) → ESCALATED` action `escalate`
- [x] `orchestrator/src/orchestrator/actions/post_acceptance_report.py`：新文件
  - `@register("post_acceptance_report", idempotent=True)`
  - 渲染 acceptance status block（jinja2 模板 + marker）
  - PATCH BKD intent issue body（不传 tags / statusId）
  - 写 `ctx.acceptance_reported_at`
- [x] `orchestrator/src/orchestrator/actions/__init__.py`：import 新 action
- [x] `orchestrator/src/orchestrator/prompts/acceptance_status_block.md.j2`：新模板
- [x] `orchestrator/src/orchestrator/webhook.py`：
  - 在 `derive_event` 返 None 且 `body.event=="issue.updated"` 后新增 PENDING fallback
  - 调 `BKDClient.get_issue` 拿当前 statusId
  - statusId == "done" → emit USER_REVIEW_PASS；in {review, blocked} → 设 ctx.escalated_reason="user-requested-fix" + emit USER_REVIEW_FIX
- [x] `orchestrator/src/orchestrator/watchdog.py`：`_SKIP_STATES` 加 `ReqState.PENDING_USER_REVIEW.value`

## Stage: docs

- [x] `docs/state-machine.md`：trans table 重生成（执行 `dump_transitions`）
- [x] `docs/user-feedback-loop.md`：§5 标注 Case 2 已落地

## Stage: tests

- [x] `orchestrator/tests/test_state.py`：USR-T1..T4 transition 校验
- [x] `orchestrator/tests/test_actions_post_acceptance_report.py`：USR-A1..A3 单测
- [x] `orchestrator/tests/test_webhook_user_review_gate.py`：USR-W1..W6 单测
- [x] `orchestrator/tests/test_watchdog_pending_user_review.py`：USR-WD1 单测
- [x] `orchestrator/tests/test_contract_user_acceptance_gate.py`：USER-S1..S6 contract 测试

## Stage: PR

- [x] git push feat/REQ-bkd-acceptance-feedback-loop-1777278984
- [x] gh pr create --label sisyphus
