# REQ-fix-verifier-schema-395-1777869659 — verifier decision JSON 硬强制 (closes #395)

Refs phona/sisyphus#395.

## 现状

`verifier-agent` 输出 decision 时偶尔图省事只 PATCH `decision:pass` 字符串 tag，
不写 base64-json，也不写 ` ```json {...} ``` ` 块。orch webhook 已经做过两手兜底：

1. `verifier_parser.extract_decision_robust` 会扫 base64 tag → JSON 块 → 平凡
   `decision:<action>[-<fixer>]` tag（REQ-fix-verifier-decision-tag-1777812498
   加的第 3 路 fallback）。
2. webhook.py:344 起的 `retry_worthy` 路径在 schema invalid 时自动 follow-up
   verifier，要求重输出标准格式 —— **但最多 1 次**（`retry_count < 2` 实测只
   触发 1 次 retry）。`_VERIFIER_RETRY_PROMPT` 也只描述结构，不点名"必须放最后
   一条 assistant message"。

5/4 v5 #802 的事故就是 retry 用完仍 schema invalid 直接 escalate、人 30+ min
才发现。issue #395 把修法切成 3 块（A=BKD-side schema validate / B=orch 层
retry 提到 3 次 / C=lint 守）。本 REQ 只交付 **B + C**：

- A（BKD 层 schema validate）属于 BKD 仓改动，不在本 sisyphus 仓 scope。
- B（orch retry 提 3 次 + prompt 更明确）：webhook.py + 单测改动。
- C（lint 守）：新增 `scripts/lint-verifier-prompts.py`，CI 跑。

## 修法

### B. webhook 层 retry follow-up cap 提到 3

`orchestrator/src/orchestrator/webhook.py` 的 `retry_worthy` 分支：

- `retry_count < 2` → `retry_count < 3`（cap 从 2 次提到 3 次）
- `_VERIFIER_RETRY_PROMPT` 加 3 条强制说明：
  1. JSON 块 **必须放在最后一条 assistant message** 里（不能写中间消息）
  2. 同时**先 PATCH `decision:<action>[-<fixer>]` tag** 再写 JSON（双兜底）
  3. 重申 `action` 只能是 `pass` / `fix` / `escalate` / `retry` 4 字面量

verifier_parse_retry_count 含义不变，只是 cap 抬高 1。

### C. scripts/lint-verifier-prompts.py + CI 集成

新脚本静态扫 `orchestrator/src/orchestrator/prompts/verifier/` 下所有
`*.md.j2`，校验关键约束：

1. `_decision.md.j2` 必须包含 4 个字面 action 名
   (`pass` / `fix` / `escalate` / `retry`) 的 JSON 示例
2. `_decision.md.j2` 必须含强制提示关键短语：
   - `HARD CONSTRAINT`
   - `decision:` tag mandate
   - `最后一条 assistant message`
3. 每个 `<stage>_<trigger>.md.j2` 必须 `{% include "verifier/_decision.md.j2" %}`

任一缺失退出非零。挂在 `orchestrator-ci.yml` lint-test job 的 `ruff` step
后面，runner 镜像不需要新依赖（纯 stdlib python3）。

## 不做的事

- 不动 BKD 层 schema validate（issue #395 A 段）—— 跨仓 scope
- 不改 verifier prompt 任何业务语义（4 路决策不动、JSON schema 不动、tag 兜
  底逻辑不动）
- 不改 watchdog / escalate 路径
- 不动 verifier_parser.extract_decision_robust（前一个 REQ 已经修好平凡 tag
  fallback，本 REQ 只补 retry 次数 + lint 守）
