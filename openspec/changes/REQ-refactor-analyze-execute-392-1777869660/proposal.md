# REQ-refactor-analyze-execute-392 — stage `analyze` → `execute` rename

## 动机

stage 命名 `analyze` 跟实际职责严重不符。

实际做的事（per CLAUDE.md M17 / docs/architecture.md）：
> analyze-agent 全责交付：写 spec + 业务码 + push feat/REQ-x + 开 PR；自决拆 sub-issue

这是端到端 execute，不是 "analyze"。`analyze` 这个动词暗示"只看不动手 / 输出报告"，但 agent 实际在写完整 commit + 开 PR。命名跟语义脱节后：

- 新人 / 读 docs/state-machine.md 看到 `ANALYZING` 以为是 brainstorm 阶段
- BKD UI 看到 tag `analyze` / `[ANALYZE]` issue title 容易误以为 agent 没动手
- 跟相邻命名 `intake`（真澄清）/ `verifier`（真审）/ `accept`（真验收）一致性差

## 提议

把 stage 的全部命名维度从 `analyze` 改成 `execute`：

| 维度 | 当前 | 改后 |
|---|---|---|
| `ReqState` 枚举 | `ANALYZING` / `ANALYZE_ARTIFACT_CHECKING` | `EXECUTING` / `EXECUTE_ARTIFACT_CHECKING` |
| state 字符串值 | `"analyzing"` / `"analyze-artifact-checking"` | `"executing"` / `"execute-artifact-checking"` |
| `Event` 枚举 | `INTENT_ANALYZE` / `ANALYZE_DONE` / `ANALYZE_ARTIFACT_CHECK_PASS/FAIL` | `INTENT_EXECUTE` / `EXECUTE_DONE` / `EXECUTE_ARTIFACT_CHECK_PASS/FAIL` |
| event 字符串值 | `"intent.analyze"` / `"analyze.done"` / `"analyze-artifact-check.{pass,fail}"` | `"intent.execute"` / `"execute.done"` / `"execute-artifact-check.{pass,fail}"` |
| stage_runs.stage | `"analyze"` / `"analyze_artifact_check"` | `"execute"` / `"execute_artifact_check"` |
| BKD tag（写） | `intent:analyze` / `analyze` / `verify:analyze` | `intent:execute` / `execute` / `verify:execute` |
| BKD tag（读） | 仅 `analyze` 系 | 双读 `analyze` + `execute` 系（兼容 1-2 周） |
| action 名 | `start_analyze` / `start_analyze_with_finalized_intent` / `create_analyze_artifact_check` / `invoke_verifier_for_analyze_artifact_check_fail` | `start_execute` / `start_execute_with_finalized_intent` / `create_execute_artifact_check` / `invoke_verifier_for_execute_artifact_check_fail` |
| 文件名 | `actions/start_analyze.py` 等 / `checkers/analyze_artifact_check.py` / `prompts/analyze.md.j2` / `prompts/verifier/analyze_*.md.j2` | 对应 `*_execute*` |

## 兼容性策略

router/webhook 读 path 一段时间内**双识别老 + 新 tag**：

- 写：sisyphus 程序仅写 `intent:execute` / `execute` / `verify:execute`
- 读：`router.derive_event` + `webhook.pass_event_for_stage` 同时识别 `analyze` / `execute`（含 `intent:` 前缀变体）
- 1-2 周后清理老 tag 识别（独立 follow-up REQ）

DB 既存数据（in-flight / 历史 REQ）**整库迁移**：migration 0017 把所有
`analyze*` 字符串值 UPDATE 成 `execute*`，**不依赖** 应用层做"读两套值"。

## 验收

- 全链 self-dogfood：本 REQ 自身用新 tag / 新 state 跑通 (analyze→execute 重构 → 推 PR → CI → accept)
- 老 BKD intent issue 仍能被 router 路由（read-compat 测试覆盖）
- docs/architecture.md / state-machine.md / CLAUDE.md / observability/queries 全部一致
- `pytest` unit + integration 全绿，无 `analyze` 残余字符串（除历史 REQ id 引用 + archive/）

## 不在范围

- BKD 端是否要给历史 issue 自动 rebrand tag —— 不做，read-compat 兜住即可
- 改 `intake` / `accept` / `verifier` 等其它 stage 命名 —— 与 `analyze` 不同，名实相符
- 改 `analyze-agent` 之外的 agent 命名

## 风险

- 漏改某处 string 字面量 → state machine 静默卡死。缓解：
  - 用 `pytest tests/test_state.py tests/test_router.py tests/test_engine.py` 覆盖所有 transition
  - grep `analyze` 残余清单在 PR description 自审
- DB migration 需整库 UPDATE → 短暂锁表。缓解：sisyphus 单实例 / 极低 QPS，影响可忽略
