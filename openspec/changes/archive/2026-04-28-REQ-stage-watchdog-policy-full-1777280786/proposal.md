# REQ-stage-watchdog-policy-full-1777280786: feat(watchdog): per-stage typed policy table — extend exemption/timeout to all states

## 问题

REQ-watchdog-stage-policy-1777269909（PR #161）只完成了 [docs/user-feedback-loop.md §1](../../../docs/user-feedback-loop.md) "Stage type taxonomy" 的最小 slice：把
`INTAKING` 拉进 `_NO_WATCHDOG_STATES` set 拍平豁免。但设计文档列的"按 stage type
分档"路线图——`STAGE_WATCHDOG_POLICY: dict[ReqState, dict | None]`——还没做：

- 当前所有非 INTAKING 的 in-flight state 全部走同一个全局 `min(watchdog_session_ended_threshold_sec=300, watchdog_stuck_threshold_sec=3600)`
  阈值。无法区分 deterministic-checker（应在秒-分级完）/ autonomous-bounded
  （agent 跑 30min 内出结果）/ external-poll（PR CI 可能 4h+）的差异化容忍度。
- 后续 `PENDING_USER_PR_REVIEW`（M0 §2）落地时还需要再加一个 human-in-loop state，
  现在的 set 只能拍平豁免，不能在 set 之上分别约束 timeout——下次扩展又要改 watchdog 内部。
- 现在 `_NO_WATCHDOG_STATES` 仅是 hint，没绑实际 timeout 语义：未来想把 PR_CI_RUNNING
  的 ended-session escalate 拉到 4h（CI 真的可能跑 1h+），全局阈值改不了——会回归
  其他 stage 5min 的 fast-lane 检测。

## 方案

把"是否豁免"和"什么阈值"统一成 stage-typed policy 表，policy 用 dataclass 表达
两个轴：

- `ended_sec: int` —— BKD session 进入非 running 状态（completed/failed/cancelled）后，
  机械层等多久 escalate。这条线对所有非豁免 stage 必须存在（替代当前全局 fast lane）。
- `stuck_sec: int | None` —— 不论 session 状态，stuck 超过此时长一律 escalate
  （慢车道，杀真死的长尾）。`None` = 不开启 slow lane（保留"不杀 running"语义）。

四类 stage 默认值（per docs/user-feedback-loop.md §1）：

| stage type | 默认 policy | 例 state |
|---|---|---|
| `human-loop-conversation` | `None`（SQL 预过滤） | `INTAKING` |
| `deterministic-checker` | `ended=300, stuck=300` | `SPEC_LINT_RUNNING` / `DEV_CROSS_CHECK_RUNNING` / `ANALYZE_ARTIFACT_CHECKING` |
| `autonomous-bounded` | `ended=300, stuck=None` | `ANALYZING` / `CHALLENGER_RUNNING` / `ACCEPT_RUNNING` / `ACCEPT_TEARING_DOWN` / `ARCHIVING` / `FIXER_RUNNING` / `REVIEW_RUNNING` |
| `external-poll` | `ended=300, stuck=14400` | `PR_CI_RUNNING` |

`STAGING_TEST_RUNNING` 名义上是 deterministic-checker（kubectl exec），但单/集成
测试常跑分钟级，把它单独归"宽松 deterministic"档：`ended=300, stuck=None`，避免
误杀长跑测试套件。

### 实现

`orchestrator/src/orchestrator/watchdog.py`：

1. 引入 `_StagePolicy` frozen dataclass + `_STAGE_POLICY: dict[ReqState, _StagePolicy | None]`
   表，覆盖目前所有非终态 in-flight state（13 条）。
2. `_NO_WATCHDOG_STATES` 退化为派生：`{state for state, policy in _STAGE_POLICY.items() if policy is None}`，
   不再硬编码。其他模块 / 测试 import 这个 frozenset 仍可用（语义不变）。
3. `_tick()` SQL 预过滤照旧把 `_SKIP_STATES ∪ _NO_WATCHDOG_STATES` 喂给 `state <> ALL($1)`。
   threshold 取 `min(所有非 None policy 的 ended_sec) ∪ min(所有非 None policy 的 stuck_sec) ∪ 全局 ended/stuck`，
   保证任一 stage 满足 escalate 条件的行都能被 SQL 返回（不会被 SQL 提前过滤掉）。
4. `_check_and_escalate()`：先解析 `_STAGE_POLICY[state]`（找不到 → fallback 到全局
   `watchdog_session_ended_threshold_sec` + `watchdog_stuck_threshold_sec`）。
   - `policy is None`（SQL 已过滤但 belt-and-suspenders）→ skip
   - 解析 BKD session 状态后：
     * session running + `policy.stuck_sec` 为 None + 未到 stuck → skip（既有"不杀长尾"语义）
     * session running + 到 `policy.stuck_sec` → escalate（慢车道触发）
     * session ended (or 无 issue) + 到 `policy.ended_sec` → escalate（快车道触发）
     * 其他 → skip（未到任一阈值）
5. 全局 `watchdog_session_ended_threshold_sec` / `watchdog_stuck_threshold_sec`
   保留作为 unmapped state 的 fallback——新增 `ReqState` 但忘补 policy 时，watchdog
   仍按全局阈值兜底，不会出现"新 state 完全无 watchdog"的失误。

### 取舍

- **保留全局 fallback 阈值**：删了它们 = 新增 ReqState 忘补 policy 表会瞬间裸奔。
  保留 = 安全网，且 backward-compat（既有 helm values 仍生效，操作员可灰度）。
- **不引入 env-overridable per-stage 字典**：`STAGE_WATCHDOG_POLICY` 用 module 常量
  表达，不走 pydantic Settings。原因：
  1. `dict[ReqState, dict | None]` 的 env 序列化（json）写起来易错，运维不可读
  2. 这层数据是"跟 stage 状态机紧耦合的代码常量"，换数值比改 helm values 更安全
     （改完跟着 spec 测试失败立刻知道）
  3. 真要 per-deployment override，操作员仍可改全局 `watchdog_*_threshold_sec`
     压低/抬高所有 unmapped fallback；或开新 REQ 调表（这才是受控变更）
- **autonomous-bounded `stuck_sec=None`，不跟 design doc 的 30min**：design doc 用
  30min 作建议，但既有 `config.py:186` 注释明确写 "sonnet analyze long tail 经常
  25-35min；30 min 阈值会 false-escalate 大量 dogfood REQ"。先把框架立起来，slow
  lane 默认仍是 None（保留当前"不杀 running"语义），后续运维数据驱动决定是否压低。
- **`STAGING_TEST_RUNNING` 单独归"宽松 deterministic"档**：理论是 mechanical checker
  （`make ci-unit-test && make ci-integration-test` 经 kubectl exec），但实测整套常跑
  10-20min。设 `stuck_sec=None` 跟 autonomous-bounded 一致，避免 false-escalate
  慢测试套件。
- **`PR_CI_RUNNING.stuck_sec=14400`（4h）是新引入的硬上限**：之前因为 per-row
  unconditional skip running 实际上是无穷。GH Actions 极少跑超过 4h；超 4h 大概率
  是 GHA self-hosted runner 死锁等。给个 hard cap 让 escalate 兜住，不至于 REQ 永远
  挂 PR_CI_RUNNING。

## 影响范围

- `orchestrator/src/orchestrator/watchdog.py` —— 新增 `_StagePolicy` + `_STAGE_POLICY`
  表，重构 `_tick()` SQL threshold 计算 + `_check_and_escalate()` per-stage 阈值判定，
  `_NO_WATCHDOG_STATES` 改派生
- `orchestrator/tests/test_watchdog.py` —— 新增 per-stage policy unit case，旧 case
  断言 stuck_sec 改用 dataclass 描述
- `orchestrator/tests/test_contract_watchdog_stage_policy_full.py` —— 新建合同测试
  WSPF-S1..S8（覆盖每类 stage 的 ended/running 行为）
- `openspec/changes/REQ-stage-watchdog-policy-full-1777280786/specs/watchdog-stage-policy-full/spec.md`
  —— 落 ADDED Requirements + 8 个 scenarios
