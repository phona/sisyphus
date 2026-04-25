## ADDED Requirements

### Requirement: engine 在 (REVIEW_RUNNING, VERIFY_PASS) 必须 close 当前 verifier stage_run

The orchestrator engine SHALL close the open `stage_runs` row for `stage='verifier'` whenever
a state-machine transition is dispatched with `cur_state == ReqState.REVIEW_RUNNING` and
`event == Event.VERIFY_PASS`. The close MUST set `outcome='pass'`, MUST happen via
`stage_runs.close_latest_stage_run`, and MUST NOT depend on `next_state` differing from
`cur_state`. This requirement closes the orphan path created because
`(REVIEW_RUNNING, VERIFY_PASS)` is declared as a self-loop in the transition table while
`apply_verify_pass` internally CAS's the REQ to a different stage.

#### Scenario: VSC-S1 VERIFY_PASS self-loop closes verifier stage_run with outcome=pass

- **GIVEN** orchestrator engine receives `(cur_state=REVIEW_RUNNING, event=VERIFY_PASS)`
- **AND** there is an open `stage_runs` row with `stage='verifier'` and `ended_at IS NULL` for the REQ
- **WHEN** `engine._record_stage_transitions` runs after CAS
- **THEN** `stage_runs.close_latest_stage_run(pool, req_id, "verifier", outcome="pass")` is called exactly once for that REQ

#### Scenario: VSC-S2 close 是 best-effort，失败只 log 不抛

- **GIVEN** `stage_runs.close_latest_stage_run` 抛异常（如 DB 暂时不可用）
- **WHEN** `engine._record_stage_transitions` 在 (REVIEW_RUNNING, VERIFY_PASS) 路径调用 close
- **THEN** 异常被 try/except 捕获并通过 `engine.stage_runs.write_failed` log warning，不向 `engine.step` 上抛，不影响主流程

### Requirement: engine 不得改变其他 verifier 决策路径的 stage_run 行为

The fix MUST be scoped to `event == Event.VERIFY_PASS` on `cur_state == ReqState.REVIEW_RUNNING`.
The orchestrator MUST continue to handle the other verifier decision events through the existing
generic close+open path: `VERIFY_FIX_NEEDED` and `VERIFY_ESCALATE` lead to a state change
(REVIEW_RUNNING → FIXER_RUNNING / ESCALATED) and SHALL still close the verifier stage_run via
`_EVENT_TO_OUTCOME` mapping (`fix` / `escalate` respectively). Other self-loop events on
REVIEW_RUNNING (e.g. `SESSION_FAILED` chained via the watchdog) MUST NOT trigger the explicit
verifier close.

#### Scenario: VSC-S3 VERIFY_FIX_NEEDED 仍走原 close+open 路径

- **GIVEN** orchestrator 收到 `(cur_state=REVIEW_RUNNING, event=VERIFY_FIX_NEEDED)` → next_state=FIXER_RUNNING
- **WHEN** `_record_stage_transitions` 跑
- **THEN** verifier stage_run 通过通用 close-on-leave 路径关闭（outcome=`fix`），fixer stage_run 通过通用 open-on-enter 路径打开

#### Scenario: VSC-S4 REVIEW_RUNNING + SESSION_FAILED self-loop 不触发 verifier close

- **GIVEN** orchestrator 收到 `(cur_state=REVIEW_RUNNING, event=SESSION_FAILED)`（transitions 表里是 self-loop → escalate action 自决）
- **WHEN** `_record_stage_transitions` 跑
- **THEN** 不调用 `close_latest_stage_run`（保留给 escalate action 决定真 escalate 后由后续 transition close）
