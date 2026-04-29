# REQ: 紧急修复 Hotfix mode——不走完整流水线

## 背景

当前紧急修复（如线上 bug）仍然走完整 sisyphus 流水线：
```
intake → analyze → spec-lint → challenger → dev-cross-check → staging-test → pr-ci-watch → accept → archive
```

对于 hotfix（1-2 行代码的紧急修复），这个流程太重了：
- 需要写完整的 openspec proposal/design/tasks
- 需要跑 challenger 写 contract test
- 需要跑 accept 验收
- 等不起

实际做法：开发人员手动 `git checkout -b hotfix/xxx`，改完直接 PR，加 `skip-ci` label，人 review 后人合。

但这样跳过了 sisyphus 的审计追踪，无法回答"这个 hotfix 有没有经过质量检查"。

## 目标

为紧急修复提供一条**轻量级但可审计**的快速通道。

## 方案

### Hotfix 入口

在 BKD UI 中新建 intent issue 时打 `intent:hotfix` tag，webhook 识别后启动 hotfix 精简流水线。

### Hotfix 专用流水线

状态机增加一条精简路径：
```
[HOTFIX_ANALYZING] → [HOTFIX_DEV_CROSS_CHECK] → [HOTFIX_STAGING_TEST] → [HOTFIX_PR_CI] → [HOTFIX_ARCHIVE]
```

跳过：
- intake（如果已有明确修复方案）
- spec-lint（不需要 openspec）
- challenger（不需要 contract test）
- accept（不需要 e2e 验收，风险由人承担）

保留：
- analyze（快速确认修复方案）
- dev-cross-check（lint 必须过）
- staging-test（单元测试必须过）
- pr-ci-watch（CI 必须绿）
- archive（审计追踪）

### Hotfix 标记和审计

- hotfix PR 必须带 `hotfix` label
- archive 时记录 `hotfix=true` 标记
- archive issue 标题加 `[HOTFIX DONE]`，tags 加 `hotfix`

### 安全约束

- hotfix 不自动合 PR（和正常 REQ 一样，人等 review）
- hotfix 不走 accept，archive 时加 warning 标记"未经过 e2e 验收"
- verifier 框架支持 hotfix 路径（apply_verify_pass / apply_verify_infra_retry 通过 ctx.hotfix 区分）

## 涉及改动

| 文件 | 改动 |
|---|---|
| `orchestrator/src/orchestrator/state.py` | 新增 7 个 hotfix 状态 + 1 个事件 + 26 条 transition |
| `orchestrator/src/orchestrator/router.py` | 识别 `intent:hotfix` tag |
| `orchestrator/src/orchestrator/webhook.py` | hotfix 入口过滤 + ctx.hotfix 初始化 |
| `orchestrator/src/orchestrator/engine.py` | STATE_TO_STAGE / AGENT_STAGES 增加 hotfix 映射 |
| `orchestrator/src/orchestrator/actions/_verifier.py` | _HOTFIX_PASS_ROUTING / _HOTFIX_RETRY_ROUTING + 支持 hotfix source state |
| `orchestrator/src/orchestrator/actions/done_archive.py` | hotfix tag + [HOTFIX DONE] 标题 |
| `docs/state-machine.md` | 更新状态/事件计数 + 新增 hotfix 文档 |
| `orchestrator/tests/` | 新增/更新测试覆盖 hotfix 路径 |
