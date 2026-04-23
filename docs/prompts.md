# Prompt 索引

> 自 v0.2 起，所有 agent prompt 都是 Jinja2 模板，存于 [orchestrator/src/orchestrator/prompts/](../orchestrator/src/orchestrator/prompts/)，由 orchestrator 在 `create_*` action 中通过 `prompts.render(template, **ctx)` 渲染后传给 BKD `follow_up_issue` 调用。
>
> 本文只是索引 + 协议；具体内容请直接看模板源码（每个模板顶部都有用途注释）。

## Stage agent 模板

每个 stage 一个主模板，通过 `actions/<stage>.py` 渲染。

| 模板 | Stage | 由谁起 |
|---|---|---|
| [analyze.md.j2](../orchestrator/src/orchestrator/prompts/analyze.md.j2) | analyze | `actions/start_analyze.py` |
| [spec.md.j2](../orchestrator/src/orchestrator/prompts/spec.md.j2) | contract-spec / acceptance-spec | `actions/fanout_specs.py` (×2 并行) |
| [dev.md.j2](../orchestrator/src/orchestrator/prompts/dev.md.j2) | dev | `actions/create_dev.py` |
| [accept.md.j2](../orchestrator/src/orchestrator/prompts/accept.md.j2) | accept | `actions/create_accept.py` |
| [done_archive.md.j2](../orchestrator/src/orchestrator/prompts/done_archive.md.j2) | archive | `actions/done_archive.py` |
| [bugfix.md.j2](../orchestrator/src/orchestrator/prompts/bugfix.md.j2) | （fixer 过渡） | `actions/_verifier.py:start_fixer`（PR4 / 后续 PR 替换为专用 fixer 模板） |

历史遗留模板（v0.1 时代）：

| 模板 | 状态 |
|---|---|
| [staging_test.md.j2](../orchestrator/src/orchestrator/prompts/staging_test.md.j2) | M1 起被 `checkers/staging_test.py` 取代；保留作回退 |
| [pr_ci_watch.md.j2](../orchestrator/src/orchestrator/prompts/pr_ci_watch.md.j2) | M2 起被 `checkers/pr_ci_watch.py` 取代；保留作回退 |

## verifier-agent 模板（M14b）

12 个 `verifier/{stage}_{trigger}.md.j2`：

| stage | success | fail |
|---|---|---|
| analyze | [verifier/analyze_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/analyze_success.md.j2) | [verifier/analyze_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/analyze_fail.md.j2) |
| spec | [verifier/spec_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/spec_success.md.j2) | [verifier/spec_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/spec_fail.md.j2) |
| dev | [verifier/dev_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/dev_success.md.j2) | [verifier/dev_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/dev_fail.md.j2) |
| staging_test | [verifier/staging_test_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/staging_test_success.md.j2) | [verifier/staging_test_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/staging_test_fail.md.j2) |
| pr_ci | [verifier/pr_ci_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/pr_ci_success.md.j2) | [verifier/pr_ci_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/pr_ci_fail.md.j2) |
| accept | [verifier/accept_success.md.j2](../orchestrator/src/orchestrator/prompts/verifier/accept_success.md.j2) | [verifier/accept_fail.md.j2](../orchestrator/src/orchestrator/prompts/verifier/accept_fail.md.j2) |

共享 partial：[verifier/_header.md.j2](../orchestrator/src/orchestrator/prompts/verifier/_header.md.j2)（场景 + req_id），[verifier/_decision.md.j2](../orchestrator/src/orchestrator/prompts/verifier/_decision.md.j2)（输出 decision JSON 协议）。

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
| analyze | `analyze`（spawn 时已带）+ `REQ-xx` |
| spec (×2) | `contract-spec` 或 `acceptance-spec` + `REQ-xx` |
| dev | `dev` + `REQ-xx` + 在 issue description 写明 PR URL |
| accept | `accept` + `result:pass` 或 `result:fail` + `REQ-xx` |
| verifier | `verifier` + `verify:<stage>` + `trigger:<success\|fail>` + `decision:<urlsafe-base64-json>` |
| fixer | `fixer` + `fixer:<dev\|spec>` + `parent-stage:<stage>` + `parent-id:<verifier_issue_id>` |
| done-archive | `done-archive` + `REQ-xx` |

加 tag 用 BKD `update-issue`（保留现有 tag，追加新 tag）。

### verifier decision JSON

verifier-agent 必须输出一段 decision JSON。两种写法（router 都接，优先 tag）：

1. **首选**：tag `decision:<urlsafe-base64(json)>`
2. **兜底**：issue description 里 ```` ```json ```` 块（取最后一个）

schema（`router.validate_decision`）：

```json
{
  "action": "pass" | "fix" | "retry_checker" | "escalate",
  "fixer": "dev" | "spec" | null,
  "confidence": "high" | "low",
  "reason": "≤ 500 字解释"
}
```

约束：
- `action=fix` 时 `fixer` 必须非 null
- `action≠fix` 时 `fixer` 必须 null
- 不合规 → `VERIFY_ESCALATE` → 终态 ESCALATED

### dev 开 PR

dev-agent 必须 push `feat/REQ-x` 分支并真开 PR（`gh pr create`）。pr-ci-watch
按 branch 名 `gh pr list --head feat/REQ-x` 找 PR，不需要任何回写步骤。

## 改 prompt 的工作流

1. 改对应 `.md.j2` 文件
2. 在 `config_version` 表插一行（`kind=prompt, target=<模板路径>, version_hash=<新版本>`）
3. 在 `improvement_log` 写假设 + 验证指标 SQL
4. 部署
5. 2 周后回填 `verdict`

详见 [observability.md §可持续改进闭环](./observability.md#可持续改进闭环)。
