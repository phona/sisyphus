# REQ-feat-intent-entrypoint-400-1777882563: feat(intent): stage entry-point tags

## 背景

closes phona/sisyphus#400

当前 `intent:` tag 只有两个：`intent:intake`（起 intake agent）和 `intent:analyze`（起 analyze agent）。
用户无法跳过前置 stage，直接从中间 stage 测试局部链路（例如：手写了实现，只想跑 staging test 验证一下）。

## 目标

扩展 `intent:` tag 为 stage entry-point，增加 4 个新入口，让 REQ 能从指定 stage 开始
跳过前面所有 stage。

新入口：

| tag | 进入 state | 跳过 stages |
|---|---|---|
| `intent:test` | `STAGING_TEST_RUNNING` | analyze / spec-lint / challenger / dev-cross-check |
| `intent:pr_ci` | `PR_CI_RUNNING` | analyze / spec-lint / challenger / dev-cross-check / staging-test |
| `intent:accept` | `ACCEPT_RUNNING` | analyze / spec-lint / challenger / dev-cross-check / staging-test / pr-ci |
| `intent:archive` | `DONE` | 全部 stage，直达 DONE |

## 设计

### State Machine
- Event 加 `INTENT_TEST / INTENT_PR_CI / INTENT_ACCEPT / INTENT_ARCHIVE`
- TRANSITIONS 加 `(INIT, intent.X)` → 对应 state

### Router
- `derive_event` 识别 `intent:test` / `intent:pr_ci` / `intent:accept` / `intent:archive` tag
- 复用现有幂等保护（如 `staging-test` not in tagset 才 fire）

### Actions 前置校验
- `intent:test` / `intent:pr_ci` / `intent:accept` 须带 `pr:owner/repo#N` tag，缺失直接 escalate
- `intent:test`：从 `pr:` tag 解出 repo + PR 号 → GH API 取 head branch → clone workspace
- `intent:pr_ci`：注入 ctx.involved_repos + ctx.branch，checker 模式直接使用
- `intent:accept`：clone workspace（复用 `_ensure_runner_pod_ready` 逻辑）

### Docs
- `docs/state-machine.md`：event 表 + mermaid 图加 4 个新 INIT 出口
- `docs/architecture.md`：入口选择段补充 4 条新 entry-point 说明

## 验收

派 `intent:test` REQ + `pr:phona/sisyphus#X` tag，sisyphus orch 直接入 STAGING_TEST_RUNNING，
跳过前 4 段。CI green。
