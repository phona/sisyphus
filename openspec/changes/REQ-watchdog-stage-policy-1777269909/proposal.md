# REQ-watchdog-stage-policy-1777269909: feat(watchdog): per-stage policy — INTAKING exempt from stuck timeout

## 问题

watchdog 当前用一个全局 `min(watchdog_session_ended_threshold_sec=300, watchdog_stuck_threshold_sec=3600)`
阈值兜底**所有** in-flight state，包括 `INTAKING`。

`INTAKING` 是**人在回路**（human-in-the-loop）的 stage：intake-agent 多轮 BKD chat
跟用户澄清需求。BKD session 在用户两次 follow-up 之间会从 `running` 落到
`completed`（agent 跑完一回合等下一轮用户输入）。一旦 user 思考超过 5min（fast lane
阈值）watchdog 就 escalate，机械超时杀掉真正想认真澄清需求的 REQ。

REQ-watchdog-intake-no-result-1777078182 （PR #65）做的"intake-no-result-tag"专属
escalate 路径加重了这个问题：本意是兜"agent 写完忘 PATCH result tag"的 prompt-bug，
实际副作用是把"用户多轮 chat 间隔"全归到这条路径，把所有未及时回复的 intake 直接
escalate 掉。

## 方案

watchdog 按 stage type 走差异化策略：

- **human-in-loop stages**（目前 `INTAKING`）：完全豁免 watchdog 兜底。机械层不
  对人在思考的 stage 设超时；人若觉得 intake 真死了，手动 admin/resume 终止。
- **agent / mechanical stages**（其他所有 in-flight state）：保持现有 `min(fast, slow)`
  阈值 + auto-resume + escalate 兜底语义不变。

### 实现

`watchdog.py` 引入 `_NO_WATCHDOG_STATES: frozenset[ReqState]` 集合，目前仅含
`ReqState.INTAKING`。watchdog 把这个集合并入 SQL `state <> ALL($1::text[])` 的
预过滤参数，**INTAKING 行根本不会被 fetch 出来**，避免后续逻辑误判。

同时回收 REQ-watchdog-intake-no-result-1777078182 引入的 intake-specific 机械超时
机器：

- `watchdog._INTAKE_RESULT_TAGS` / `_INTAKE_NO_RESULT_EVENT` / `_INTAKE_NO_RESULT_REASON`
  常量删除；
- `watchdog._is_intake_no_result_tag(...)` 纯函数删除；
- `watchdog._check_and_escalate(...)` 内部 intake-no-result-tag 分支删除；
- `actions/escalate.py::_SESSION_END_SIGNALS` 集合中移除 `"watchdog.intake_no_result_tag"`
  字符串（dead code，watchdog 不再 emit 这个 body.event）。

### 取舍

- **不保留 intake-no-result-tag 检测**：原 detection 无法区分"agent 真忘 tag"和
  "用户在思考"两种形态——session_status="completed" + 无 result tag 是两者**共有
  的**。观测下发现误判远多于真命中（用户少有 5min 内连续打字的）。直接退化"机械
  不杀 intake"更安全，"prompt 实现 bug 不收尾"由 spec_lint / dev_cross_check / 人
  review 兜，不靠 watchdog。
- **不引入 per-state 不同阈值**：当前只有 `INTAKING` 需要差异化，set 一行就够。
  真要支持多档阈值再引 `dict[ReqState, int]`，YAGNI。
- **_SKIP_STATES 不直接吞 INTAKING**：`_SKIP_STATES` 是终态/未入链语义，混进
  INTAKING 会污染语义。新 set `_NO_WATCHDOG_STATES` 单独表达 "human-in-loop 豁免"。

## 影响范围

- `orchestrator/src/orchestrator/watchdog.py` — 新增豁免集合 + SQL 合并 + 删 intake 死代码
- `orchestrator/src/orchestrator/actions/escalate.py` — 移除死 body.event 字符串
- `orchestrator/tests/test_watchdog.py` — 改 INTAKING 相关 case，加 INTAKING-skip 验证
- `orchestrator/tests/test_contract_watchdog_intake_no_result.py` — 删除（contract 失效）
- `openspec/changes/REQ-watchdog-intake-no-result-1777078182/` — 删除（前 REQ 被本 REQ 取代，未归档故安全）
