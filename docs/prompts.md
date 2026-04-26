# Prompt 索引

> 自 v0.2 起，所有 agent prompt 都是 Jinja2 模板，存于 [orchestrator/src/orchestrator/prompts/](../orchestrator/src/orchestrator/prompts/)，由 orchestrator 在 `actions/start_*` / `actions/create_*` action 中通过 `prompts.render(template, **ctx)` 渲染后传给 BKD `follow_up_issue` 调用。
>
> 本文只是索引 + 协议；具体内容请直接看模板源码（每个模板顶部都有用途注释）。

## Stage agent 模板

每个 stage 一个主模板，通过 `actions/<stage>.py` 渲染。

| 模板 | Stage | 由谁起 |
|---|---|---|
| [intake.md.j2](../orchestrator/src/orchestrator/prompts/intake.md.j2) | **intake**（物理隔离 brainstorm）| `actions/start_intake.py` |
| [analyze.md.j2](../orchestrator/src/orchestrator/prompts/analyze.md.j2) | analyze（M17 全责交付：spec + 业务码 + push + 开 PR + unit test 都在一段里完成；analyze-agent 自决是否开 BKD sub-issue 平行干） | `actions/start_analyze.py`（直接入口）/ `actions/start_analyze_with_finalized_intent.py`（intake 后入口） |
| [challenger.md.j2](../orchestrator/src/orchestrator/prompts/challenger.md.j2) | challenger（M18：黑盒读 spec 写 contract test，**不看 dev 代码**；spec 自相矛盾 / 写不出 test → fail → verifier） | `actions/start_challenger.py` |
| [accept.md.j2](../orchestrator/src/orchestrator/prompts/accept.md.j2) | accept | `actions/create_accept.py` |
| [done_archive.md.j2](../orchestrator/src/orchestrator/prompts/done_archive.md.j2) | archive（每仓 `openspec apply` + 关 issue；**不自动合 PR / 不 push main**） | `actions/done_archive.py` |
| [bugfix.md.j2](../orchestrator/src/orchestrator/prompts/bugfix.md.j2) | （fixer 过渡） | `actions/_verifier.py:start_fixer`（后续替换为专用 dev/spec fixer 模板） |

历史遗留模板（v0.1 时代，保留作回退）：

| 模板 | 状态 |
|---|---|
| [staging_test.md.j2](../orchestrator/src/orchestrator/prompts/staging_test.md.j2) | M1 起被 `checkers/staging_test.py` 取代；保留作回退 |
| [pr_ci_watch.md.j2](../orchestrator/src/orchestrator/prompts/pr_ci_watch.md.j2) | M2 起被 `checkers/pr_ci_watch.py` 取代；保留作回退 |

> M16 起 sisyphus 不再起 spec / dev BKD 子 agent，曾经的 `spec.md.j2` / `dev.md.j2` /
> `actions/fanout_specs.py` / `actions/create_dev.py` 已删；M17 起由 analyze-agent
> 全责交付（写 spec + 写业务码 + push + 开 PR），如需并行 analyze-agent 自决开 BKD sub-issue。

## verifier-agent 模板（M14b）

14 对 `verifier/{stage}_{trigger}.md.j2`（7 stage × success/fail），加 3 个共享 partial：

| stage | success | fail |
|---|---|---|
| analyze | [verifier/analyze_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/analyze_success.md.j2) | [verifier/analyze_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/analyze_fail.md.j2) |
| spec_lint (M15) | [verifier/spec_lint_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/spec_lint_success.md.j2) | [verifier/spec_lint_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/spec_lint_fail.md.j2) |
| challenger (M18) | [verifier/challenger_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/challenger_success.md.j2) | [verifier/challenger_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/challenger_fail.md.j2) |
| dev_cross_check (M15) | [verifier/dev_cross_check_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/dev_cross_check_success.md.j2) | [verifier/dev_cross_check_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/dev_cross_check_fail.md.j2) |
| staging_test | [verifier/staging_test_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/staging_test_success.md.j2) | [verifier/staging_test_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/staging_test_fail.md.j2) |
| pr_ci | [verifier/pr_ci_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/pr_ci_success.md.j2) | [verifier/pr_ci_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/pr_ci_fail.md.j2) |
| accept | [verifier/accept_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/accept_success.md.j2) | [verifier/accept_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/accept_fail.md.j2) |

共享 partial：
- [verifier/_header.md.j2](../orchestrator/src/orchestrator/prompts/verifier/_header.md.j2) —— 场景 + req_id 通用头
- [verifier/_decision.md.j2](../orchestrator/src/orchestrator/prompts/verifier/_decision.md.j2) —— 输出 decision JSON 协议
- [verifier/_audit.md.j2](../orchestrator/src/orchestrator/prompts/verifier/_audit.md.j2) —— after-fix 二次 verify 时用的 audit 字段（fixer 是否作弊：test-hack / code-lobotomy / spec-drift / legitimate / unclear）

## 共享 partial

[\_shared/](../orchestrator/src/orchestrator/prompts/_shared/) 下：
- `runner_container.md.j2` —— runner pod 路径 / 环境约定
- `tools_whitelist.md.j2` —— 允许 agent 调的工具
- `self_issue_constraint.md.j2` —— 自指 issue 限制（防 loop）

## 输出协议（agent 怎么报告结果）

### tag 协议

router.py **只看 tag 不看 title**，title 仅供人看。详细 tag 命名见 [api-tag-management-spec.md](./api-tag-management-spec.md)，简表：

| stage agent | 必加 tag |
|---|---|
| intake | `intake` + `REQ-xx`；完成时 + `result:pass`（或 `result:fail`） |
| analyze | `analyze`（spawn 时已带）+ `REQ-xx` |
| challenger | `challenger` + `REQ-xx`；完成时 + `result:pass` 或 `result:fail` |
| accept | `accept` + `result:pass` 或 `result:fail` + `REQ-xx` |
| verifier | `verifier` + `verify:<stage>` + `trigger:<success\|fail>` + `decision:<urlsafe-base64-json>` |
| fixer | `fixer` + `fixer:<dev\|spec>` + `parent-stage:<stage>` + `parent-id:<verifier_issue_id>` + `round-N` |
| done-archive | `done-archive` + `REQ-xx` |

加 tag 用 BKD `update-issue`（保留现有 tag，追加新 tag）。

### intake finalized intent JSON

intake-agent 必须在最后一条 chat message 里输出 finalized intent JSON（ ```json 块），再 PATCH `result:pass` tag。

schema（`router.extract_intake_finalized_intent`，必须含全 6 字段）：

```json
{
  "involved_repos": ["owner/repo-a"],
  "business_behavior": "用户视角的行为描述（一两句话）",
  "data_constraints": "字段 / endpoint / 错误格式 / 命名约定",
  "edge_cases": "边界 / 错误 / 不能",
  "do_not_touch": "防止 agent 顺手重构撞坏的范围",
  "acceptance": "怎么算实现完，验收命令"
}
```

解析失败（字段缺失 / JSON 格式错误）→ sisyphus 降级为 `INTAKE_FAIL` → ESCALATED。

### verifier decision JSON

verifier-agent 必须输出一段 decision JSON。两种写法（router 都接，优先 tag）：

1. **首选**：tag `decision:<urlsafe-base64(json)>`
2. **兜底**：issue description 里 ```` ```json ```` 块（取最后一个）

schema（`router.validate_decision`）：

```json
{
  "action": "pass" | "fix" | "escalate",
  "fixer": "dev" | "spec" | null,
  "confidence": "high" | "low",
  "reason": "≤ 500 字解释"
}
```

> retry_checker 已砍。基础设施 flaky / 外部抖动 → `escalate`，由人介入重新触发。

约束：
- `action=fix` 时 `fixer` 必须非 null
- `action≠fix` 时 `fixer` 必须 null
- 不合规 → `VERIFY_ESCALATE` → 终态 ESCALATED

### dev 开 PR

M17 起 analyze-agent 必须 push `feat/REQ-x` 分支并真开 PR（`gh pr create`）。pr-ci-watch
按 branch 名 `gh pr list --head feat/REQ-x` 找 PR，不需要任何回写步骤。

## 改 prompt 的工作流

1. 改对应 `.md.j2` 文件
2. 在 `config_version` 表插一行（`kind=prompt, target=<模板路径>, version_hash=<新版本>`）
3. 在 `improvement_log` 写假设 + 验证指标 SQL
4. 部署
5. 2 周后回填 `verdict`

详见 [observability.md §可持续改进闭环](./observability.md#可持续改进闭环)。
