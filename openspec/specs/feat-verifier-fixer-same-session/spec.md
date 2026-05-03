# feat-verifier-fixer-same-session Specification

## Purpose

当 verifier-agent 判决 `decision=fix` 后，通过 BKD `follow-up` API 在同一 verifier issue
session 内续 turn 执行修复，而不是另起独立 fixer issue。目标是消除 fixer 第一 turn 重读
verifier 上下文的 token 开销（若 `agent_turns` 数据证明 first-turn 重读占比 ≥ 30%）。

本提案仅设计改动面，**不触发任何生产行为变更**。实现 PR 合入须满足末尾
Readiness Gate 的所有前置条件。

## Background

当前流程（`actions/_verifier.py:start_fixer`）：

1. verifier session 完成 → webhook 解 decision JSON → emit VERIFY_FIX
2. `start_fixer` 新建独立 fixer issue，渲染 `bugfix.md.j2` prompt，再调
   `follow_up_issue(fixer_issue_id, prompt)` 发送 prompt
3. fixer agent 第一 turn 通常需重读 verifier issue 的失败日志与决策上下文

Same-session continuation（本提案）：

1. 同上，但 VERIFY_FIX 时改调
   `bkd.follow_up_issue(verifier_issue_id, fixer_prompt)`
2. BKD 在原 verifier issue session 追加 follow-up turn，agent 天然延续已加载的
   失败日志与决策上下文，不需重读
3. session.completed webhook 仍触发 FIXER_DONE 事件，状态机推进语义不变

## Requirements

### Requirement: start_fixer 按 feature flag 选择 continuation 或 legacy 路径

`start_fixer` action handler MUST 支持 feature flag `same_session_fixer_enabled`
（settings 控制，默认 `False`）。当 flag 为 `True` 时，系统 SHALL 调用
`bkd.follow_up_issue(project_id, verifier_issue_id, fixer_prompt)`，不调用
`bkd.create_issue`。当 flag 为 `False` 时，系统 MUST 保持当前 `start_fixer` 行为
完全不变（向后兼容）。无论 flag 取何值，round cap 检测 SHALL 先于
follow_up / create_issue 执行；`fixer_round` MUST 在每次 follow-up turn 后递增。

#### Scenario: VFSS-S1 flag=True 下 dev fixer 走 continuation 不建新 issue

- **GIVEN** `same_session_fixer_enabled=True`，state is REVIEW_RUNNING，verifier
  decision `{"action": "fix", "fixer": "dev", "scope": "src/", "reason": "unit test
  assertion wrong", "confidence": "high"}`，`ctx.fixer_round=0`，
  `settings.fixer_round_cap=3`
- **WHEN** `start_fixer` action fires
- **THEN** `bkd.follow_up_issue(project_id, verifier_issue_id, fixer_prompt)` MUST
  be called
- **AND** `bkd.create_issue` MUST NOT be called
- **AND** `ctx.fixer_round` increments to 1
- **AND** BKD session.completed webhook fires FIXER_DONE，状态机推进至
  REVIEW_RUNNING（invoke_verifier_after_fix）

#### Scenario: VFSS-S2 flag=True 下 spec fixer 走 continuation

- **GIVEN** `same_session_fixer_enabled=True`，verifier decision
  `{"action": "fix", "fixer": "spec", "scope": "openspec/changes/REQ-x/",
  "reason": "scenario refs missing", "confidence": "high"}`，`ctx.fixer_round=0`
- **WHEN** `start_fixer` action fires
- **THEN** `bkd.follow_up_issue` MUST be called with fixer_prompt rendered from
  `_followup_fix.md.j2` with `fixer=spec, scope=openspec/changes/REQ-x/`
- **AND** `ctx.fixer_issue_id` SHALL equal `ctx.verifier_issue_id`
- **AND** subsequent `invoke_verifier_after_fix` uses `source_issue_id=verifier_issue_id`

#### Scenario: VFSS-S3 round cap 触发时 continuation 路径也 escalate

- **GIVEN** `same_session_fixer_enabled=True`，`ctx.fixer_round=3`，
  `settings.fixer_round_cap=3`
- **WHEN** `start_fixer` fires（next_round=4 > cap=3）
- **THEN** system MUST emit `VERIFY_ESCALATE` with `reason=fixer-round-cap`
- **AND** `bkd.follow_up_issue` MUST NOT be called

#### Scenario: VFSS-S4 flag=False 保持 legacy 行为不变

- **GIVEN** `same_session_fixer_enabled=False`（默认值）
- **WHEN** `start_fixer` fires with `decision action=fix`
- **THEN** `bkd.create_issue` MUST be called（legacy path）
- **AND** `verifier_decisions.same_session` SHALL default to FALSE

### Requirement: follow-up payload 模板合并骨架

`prompts/verifier/_followup_fix.md.j2` SHALL 作为 continuation 路径的 follow-up
payload 模板，将 verifier 判决摘要（stage / fixer / scope / reason 字段）与修复指令
合并为单一 prompt。模板 MUST 保留 `bugfix.md.j2` 的全部硬规则（不得删 assertion、
禁止 Skip/hardcode/注释掉逻辑、禁加 `result:*` tag）。模板 SHALL 包含 Step 0
环境短路检测段落和 feat/REQ push 指令段落，与 `bugfix.md.j2` 保持一致。

#### Scenario: VFSS-S5 follow-up prompt 包含 verifier 决策摘要与修复指令

- **GIVEN** verifier decision `{"fixer": "dev", "scope": "src/", "reason": "nil pointer"}`
- **WHEN** `_followup_fix.md.j2` is rendered with the decision context
- **THEN** rendered prompt MUST contain the verifier decision summary section with
  stage, fixer, scope, and reason fields
- **AND** rendered prompt MUST contain Step 0 environment short-circuit check
- **AND** rendered prompt MUST contain push instruction to feat/REQ branch

### Requirement: verifier_decisions 新增 same_session 标识列

`verifier_decisions` 表 SHALL 新增列 `same_session BOOLEAN NOT NULL DEFAULT FALSE`，
标识本次 fixer 是否走 continuation 路径。`decision_fixer` 列（`dev`/`spec`）MUST 保留，
供 Q9/Q16 看板继续使用。Q9/Q12/Q13/Q14/Q16 依赖的全部字段 SHALL 不受本提案破坏；
Q23 first-turn token composition SQL MUST 以 `turn_index > 0` 区分 same-session
fixer turn 与 verifier 初始 turn。

#### Scenario: VFSS-S6 same-session 模式下 verifier_decisions 写 same_session=TRUE

- **GIVEN** `same_session_fixer_enabled=True`，decision action=fix，fixer=dev
- **WHEN** `start_fixer` writes to `verifier_decisions`
- **THEN** row MUST have `same_session=TRUE, decision_fixer='dev'`
- **AND** `fixer_issue_id` in ctx SHALL equal verifier_issue_id（非新建 issue）

#### Scenario: VFSS-S7 legacy 模式下 verifier_decisions same_session 默认 FALSE

- **GIVEN** `same_session_fixer_enabled=False`
- **WHEN** `start_fixer` writes to `verifier_decisions`
- **THEN** row MUST have `same_session=FALSE`
- **AND** `fixer_issue_id` SHALL be the newly created fixer issue id

## Readiness Gate

实现 PR 合入前 MUST 满足如下所有前置条件，未满足时 PR SHALL 不合入 production：

- [ ] phona/sisyphus#241（agent_turns 表）落地至少 14 天，数据 ≥ 100 条 fixer turn
- [ ] Q23（first-turn token composition SQL）可跑，能给出 fixer first-turn 重读占比
- [ ] 数据阈值决策：
  - 占比 **≥ 30%**：上下文断点真痛 → 落本提案（`same_session_fixer_enabled` 切 `True`）
  - 占比 **15%–30%**：折中 → 只在 `_decision.md.j2` 里把判决摘要塞进 fixer prompt，
    不启用 same-session continuation
  - 占比 **< 15%**：体感不是事实 → 关 phona/sisyphus#240，本提案不实现
- [ ] 回归测试：flag=False / flag=True 两路均有单元 scenario 覆盖（VFSS-S1 至 VFSS-S7）
- [ ] migration 新增 `verifier_decisions.same_session` 列已在 staging 环境验证
