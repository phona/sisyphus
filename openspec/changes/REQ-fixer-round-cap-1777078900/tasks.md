# Tasks: REQ-fixer-round-cap-1777078900

## Stage: config

- [x] `orchestrator/src/orchestrator/config.py`：新增 `fixer_round_cap: int = 5` setting（env `SISYPHUS_FIXER_ROUND_CAP`）

## Stage: state machine

- [x] `orchestrator/src/orchestrator/state.py`：新增 `(FIXER_RUNNING, VERIFY_ESCALATE) → Transition(ESCALATED, "escalate", ...)` transition，让 start_fixer 能 chained-emit 进 escalate

## Stage: implementation (engine)

- [x] `orchestrator/src/orchestrator/actions/_verifier.py:start_fixer`：
  - 在创建 fixer issue 之前读 `ctx.fixer_round` (default 0)，算 `next_round`
  - 若 `next_round > settings.fixer_round_cap` → 写 `escalated_reason=fixer-round-cap` + `fixer_round_cap_hit=cap` 到 ctx，return `{"emit": "verify.escalate"}`
  - 否则用 `next_round` 作为 round 号建 issue（tag `round:N`、bugfix prompt `ROUND=N`）+ 持久化 `fixer_round=next_round`
- [x] `orchestrator/src/orchestrator/actions/escalate.py`：
  - 新增 `_HARD_REASONS = {"fixer-round-cap"}`
  - reason 解析优先级：`ctx hard reason` > `body.event ∈ canonical signals` > `ctx soft reason` > `body event slug`
  - `_is_transient`：`reason ∈ _HARD_REASONS` → 永远返 False（不 auto-resume）

## Stage: implementation (watchdog)

- [x] `orchestrator/src/orchestrator/watchdog.py:_check_and_escalate`：emit SESSION_FAILED 之前检查 `state == FIXER_RUNNING and ctx.fixer_round >= settings.fixer_round_cap`，若命中：
  - `req_state.update_context(escalated_reason="fixer-round-cap", fixer_round_cap_hit=cap)`
  - 把 ctx 内存副本同步标记
  - log `watchdog.fixer_round_cap_hit`

## Stage: tests

- [x] `orchestrator/tests/test_state.py`：EXPECTED 表加 `(FIXER_RUNNING, VERIFY_ESCALATE) → ESCALATED + escalate`
- [x] `orchestrator/tests/test_verifier.py`：
  - `test_start_fixer_persists_round_counter` —— 多次调用累计 round + 写 ctx + bugfix prompt 渲 ROUND
  - `test_start_fixer_caps_at_default_5` —— round=5 + 第 6 次调 → emit verify.escalate，不创 fixer
  - `test_start_fixer_cap_respects_setting_override` —— monkeypatch cap=2，验运维覆盖生效
  - `test_start_fixer_first_round_with_no_ctx_field` —— ctx 没字段时视为 0
- [x] `orchestrator/tests/test_actions_smoke.py`：
  - `test_escalate_fixer_round_cap_is_hard_reason` —— body.event=watchdog.stuck + ctx hard reason → 不 auto-resume
  - `test_escalate_fixer_round_cap_session_completed_path` —— body.event=session.completed 路径
  - `test_is_transient_treats_fixer_round_cap_as_hard` —— 单测 `_is_transient`
- [x] `orchestrator/tests/test_watchdog.py`：
  - `test_fixer_round_cap_marks_reason` —— FIXER_RUNNING + round=cap → 写 escalated_reason
  - `test_fixer_round_below_cap_does_not_mark` —— round<cap → 不写

## Stage: spec

- [x] `openspec/changes/REQ-fixer-round-cap-1777078900/proposal.md`
- [x] `openspec/changes/REQ-fixer-round-cap-1777078900/tasks.md`
- [x] `openspec/changes/REQ-fixer-round-cap-1777078900/specs/fixer-round-cap/contract.spec.yaml`
- [x] `openspec/changes/REQ-fixer-round-cap-1777078900/specs/fixer-round-cap/spec.md`

## Stage: PR

- [x] git push feat/REQ-fixer-round-cap-1777078900
- [x] gh pr create
