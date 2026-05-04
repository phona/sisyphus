# Proposal: state-machine silent-drop lint v2 (closes #376)

## Problem

5/4 v5 卡死链路（issue #376 实证）：

1. verifier #802 emit `decision:pass` 字符串 tag
2. router `derive_verifier_event` 解析失败 → 返 `Event.VERIFY_ESCALATE`
3. webhook 派 VERIFY_ESCALATE 给 ESCALATED state
4. `TRANSITIONS[(ESCALATED, VERIFY_ESCALATE)]` 是 `Transition(ESCALATED, None, ...)` —— self-loop, 不动
5. **零日志、零反馈、REQ 永久卡 ESCALATED**

事故根因：state 表里"合法 transition"和"语义上让 REQ 推不动的 transition"在静态层面
没区分。router 派事件成功 + state.decide() 返合法 transition，所有信号都"对" ——
但语义是死循环。需要把这种"显式 no-progress"transition 在静态层面挑出来供 review。

## Solution

`Transition` dataclass 加 `progress` 字段（`"yes"` / `"no"` / `"explicit-noop"` /
`None` 自动推导），新增 `scripts/lint-state-transitions.py` 在 dev / CI 期遍历
`TRANSITIONS` 表：

1. **自动分类**：
   - `next_state != src_state` → 派生 progress = `"yes"`（推进）
   - `next_state == src_state` → "raw no-progress"，要求显式 `progress` 字段
2. **强制显式**：raw no-progress transition 若没声明 `progress`，lint fail。
   声明值必须是 `"no"`（候选死锁，需 telemetry）或 `"explicit-noop"`（acknowledged
   intentional self-loop）。
3. **一致性校验**：
   - `progress="yes"` 但 `next_state == src_state` → 矛盾，fail
   - `progress="no"` / `"explicit-noop"` 但 `next_state != src_state` → 矛盾，fail
4. **报告**：人类可读输出，按 progress 分桶列举所有 transition，便于 code review。
5. **CI gate**：`make ci-lint` 包含本 lint，`.github/workflows/orchestrator-ci.yml`
   上一步执行；新加 transition 必须显式标注 progress 才能合 PR。

第一次跑就把今晚 `(ESCALATED, VERIFY_ESCALATE) → ESCALATED` 标 `explicit-noop`
（语义：用户 follow-up 后 verifier 仍判 escalate，留原地等下一次，是 intentional），
跟 #372 decode-fail telemetry 配合，未来 `progress="no"` 桶非空时 emit 警告。

## Why not 方案 B（运行期 metric）

只跑运行期 metric（"卡 ESCALATED 超 N 分钟告警"）也能发现死循环，但晚了一拍 ——
新加 transition 时就该在 PR review 阶段被人类看见 "这条是 no-progress, 真的对吗？"。
静态 lint 让 transition 表本身成为"已审"的 source-of-truth，运行期 metric 是兜底。
两者互补，本 REQ 先做前者（成本低，价值高）。

## Why not 方案 C（运行时 trace）

orch 已经有 stage_runs / verifier_decisions 两表收集运行轨迹（observability/）。
但表里"REQ 卡 ESCALATED"和"REQ 在 ESCALATED 反激活后正常推进"长得一样 —— 缺少
"transition 应当推进却原地踏步"这一**意图**信号。本 REQ 把意图编码进 transition 表
本身（progress 字段），让任何看 state.py 的人秒懂"这条故意 no-progress"vs"这条本该推进"。

## Scope

- `orchestrator/src/orchestrator/state.py` — `Transition` 加 `progress` 字段；
  所有 self-loop transition 标 `explicit-noop`（含 SESSION_FAILED dict comp、
  `(ESCALATED, VERIFY_ESCALATE)`、`(REVIEW_RUNNING, VERIFY_INFRA_RETRY)`）
- `scripts/lint-state-transitions.py`（新）—— 遍历 TRANSITIONS 校验 + 报告
- `Makefile` — `ci-lint` target 调本 lint
- `.github/workflows/orchestrator-ci.yml` — 加 lint step
- `orchestrator/tests/test_lint_state_transitions.py`（新）—— 单测覆盖：
  推进 transition / 显式 no-progress / 矛盾 / 缺失 annotation 四类 case

## Out of scope

- 运行期"卡 ESCALATED 超 N 分钟"告警（#372 telemetry 范围）
- 新加 transition 类型（如 progress="warn"）—— 本 REQ 只引入 yes/no/explicit-noop 三值
- TRANSITIONS 之外的 state 反激活字典（`_ESCALATED_RESUME_EVENT_SOURCES`）—— 那些 entry
  通过 `TRANSITIONS.update` 复用源 transition object，progress 字段自动继承
