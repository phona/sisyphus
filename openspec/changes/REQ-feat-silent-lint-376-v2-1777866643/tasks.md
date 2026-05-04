# Tasks

## Stage: spec
- [x] author specs/state-transition-progress-lint/spec.md scenarios

## Stage: implementation
- [x] orchestrator/src/orchestrator/state.py: `Transition` dataclass 加 `progress: str | None = None` 字段
- [x] orchestrator/src/orchestrator/state.py: 标注所有 self-loop transition 的 progress
  - `(ESCALATED, VERIFY_ESCALATE)` → `explicit-noop`
  - `(REVIEW_RUNNING, VERIFY_INFRA_RETRY)` → `explicit-noop`
  - SESSION_FAILED dict comp 每条 → `explicit-noop`
- [x] scripts/lint-state-transitions.py: 新建 lint 脚本
- [x] Makefile: `ci-lint` target 加调用 `python3 scripts/lint-state-transitions.py`
- [x] .github/workflows/orchestrator-ci.yml: lint-test job 加 lint step
- [x] orchestrator/tests/test_lint_state_transitions.py: 单测覆盖（STPL-S1..S6 + bonus）

## Stage: PR
- [ ] make ci-lint 全绿（含新 lint script）
- [ ] make ci-unit-test 全绿
- [ ] make ci-integration-test 全绿（无 PG → exit 5 → pass）
- [ ] git push origin feat/REQ-feat-silent-lint-376-v2-1777866643
- [ ] gh pr create --label sisyphus 含 sisyphus footer
