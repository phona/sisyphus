# 当前工作流状态（v3.1）

## 系统组件

```
n8n (vm-node04 K3s, workflow id 6PHyyAH82TL5Rl2i, 48 nodes)
  ├── /webhook/bkd-events          — BKD session.completed/failed (主循环)
  └── /webhook/bkd-issue-updated   — BKD issue.updated (intent:analyze 入口)

BKD (Coder Workspace)
  ├── webhook → n8n (上面两个端点)
  └── projects/workflowtest (ubox-crosser 仓库, id 77k9z58j)

调试环境 (vm-node04)
  └── aissh MCP（agent 侧透传 + n8n 侧直连）
```

**变更**：v3-entry + v3-events 合并为单 workflow v3.1，84 节点 IF 链瘦身到 48 节点（含 Router Code 节点 + 汇总路由矩阵）。legacy workflow 已删除。

## 完整流程（REQ-669 首次跑通的 happy-path，~18 min）

```
用户发需求（tags=[intent:analyze]）
    ↓ /webhook/bkd-issue-updated
[ENTRY] intent Ctx
    ↓ 只有 intent:analyze 且无 analyze tag 才放行（去抖）
[ANZ] Cr → Id → Fu → St  (创建 [REQ-xx] [ANALYZE]，下发 prompt + REQ-id)
    ↓ session.completed (tags=analyze, REQ-xx, layer:backend)
[FAN] Mark analyze done → Cr(3 specs) → Split Out → Id → Fu → St   (广播 dev/contract/accept-spec)
    ↓ analyze issue 被推到 done，后续重放走 priorStatusId==='done' 跳
    ↓ 3 个 spec session.completed (tags=*-spec, ci-passed*)
[SPG] List REQ specs → Gate check (expectedCount=3)
    ↓ 3/3 ready
[SPG] Dev Cr → Id → Fu → St   (创建 [DEV])
    ↓ session.completed (tags=dev, REQ-xx)
[CI] Cr(target=unit) → Id → Fu → St   (ci-runner: lint+unit)
    ↓ session.completed (tags=ci, target:unit, ci:pass, parent:dev, parent-id:xxx)
Router → create_ci_runner(integration) → [CI] same block
    ↓ session.completed (tags=ci, target:integration, ci:pass)
Router → escalate "AI-QA not implemented"（accept 阶段待接入）
```

accept/done 阶段尚未实现（escalate 为终点）。

## Router（pure function in `router/router.js`, 34 unit tests green）

Router 是 Code 节点里的纯函数 `routeEvent(body) → { action, params }`。十个 action：

| action | 触发 | 下游节点 |
|---|---|---|
| `start_analyze` | intent:analyze 入口且 issue 没 analyze tag | [ANZ] block |
| `fanout_specs` | analyze 完成 + 有 layers | [FAN] block |
| `mark_spec_reviewed` | **TEMP**: 任何 spec 完成 → 跳过 ci-lint，直接 ci-passed（见下方遗留） | SPG 汇合 |
| `create_ci_runner` | dev 完成（target=unit）、ci-unit 成功（target=integration）、reviewer 成功（target=integration） | [CI] block |
| `comment_back` | ci-unit 失败 → 回写 dev issue 评论 | [CB] comment-back |
| `create_bugfix` | ci-integration 失败 / accept 失败 round<3 | [BUG] block |
| `create_test_fix` | bugfix 完成 且非 spec-bug | [TFIX] block |
| `create_reviewer` | test-fix 完成 | [RVW] block |
| `skip` | priorStatusId==='done' / dedup hit / 无 spec stage | — |
| `escalate` | unsupported / 缺 layer / reviewer fail / accept round>=3 / ci-integration pass（AI-QA 待接入） | [ESC] |

Router 关键字段从 `tags` 而非 `title` 解析：
- `routeKey`：阶段优先级 `ci > test-fix > bugfix > reviewer > verify > accept > dev > analyze > spec(*-spec)`
- `resultKey`：`decision:*` / `diagnosis:*` / `result:*` / `ci:*`
- `reqId`：`REQ-xxx` 正则
- `round`：`round-N`
- `target`：`target:unit|integration|lint`
- `layers`：多 `layer:xxx` 聚合
- `parentStage` / `parentIssueId`：从 ci-runner 自己的 `parent:dev` + `parent-id:xxx` tag 解析（替代原来不稳的 webhook metadata）

## 幂等闸门（今天加的三道）

| 闸门 | 位置 | 作用 |
|---|---|---|
| **priorStatusId==='done'** | Router L1 | 最强 — issue 已经 done 则任何重放都 skip |
| **dedup (workflowStaticData)** | [ENTRY] Ctx | TTL=30min。key=`issueId\|event\|tags_sorted`，命中则整体 skip。应对 BKD 重发。 |
| **[FAN] Mark analyze done** | fanout 首步 | 主动把 analyze issue 推到 done，后续重放命中闸 1。 |

## parent-id 上链

老方案用 webhook `metadata.parentIssueId`，但 BKD webhook payload 不稳定。现在一律走 tag：

1. [CI] Cr / [BUG] Cr 写 issue 时 tag 里就带 `parent:<stage>` + `parent-id:<父 issue id>`
2. session.completed 重放时 Router 从 **自己的 tags** 解析父链，不依赖 webhook 额外字段
3. `comment_back` 动作用这个 id 定位到原 dev/spec issue 回评论

## CI gate 新旧路径对比

| 事件 | 老路径（legacy） | v3.1 |
|---|---|---|
| spec 完成 | ci-runner(lint) → mark_reviewed | **TEMP 短路：直接 mark_reviewed**（见遗留 #1）|
| dev 完成 | ci-runner(unit)，unit 失败 comment_back 到 dev | 同 |
| ci-unit pass | ci-runner(integration) | 同 |
| ci-integration pass | accept（待接入） | escalate "AI-QA pending" |
| ci-integration fail | create_bugfix round 1 | 同 |

ci-runner 用 `make ci-lint` / `ci-unit-test` / `ci-integration-test`，挂了 `BASE_REV=origin/master` 给 golangci-lint 做增量扫描。ubox-crosser 仓库已有 baseline 8 个 lint issue，但因 `--new-from-rev` 生效，只要新增 commit 干净就不报。

## 工具白名单注入（Fu prompt 前置）

每个 follow-up-issue 的 prompt 前面都强制塞一段：

```
## 工具白名单 (HARD CONSTRAINT)
仅允许: mcp__bkd__* / mcp__aissh-tao__*
绝对禁止: mcp__vibe_kanban__* / mcp__erpnext__* / Task / Agent / 其他未列出 MCP
```

原因：BKD agent session 里加载了一堆多余 MCP，早期 agent 错调用 vibe_kanban 创建 issue 污染另一个 kanban。

## 中断/回滚三层模型

| 粒度 | 手段 | 效果 |
|---|---|---|
| 单 issue | `mcp__bkd__cancel-issue` | 杀当前 session 进程，状态保留 |
| 整条 REQ 链 | 给所有 REQ-xxx tag issue 批量 cancel + 推 done | 后续 webhook 全部命中 priorStatusId 闸 |
| 全局 | n8n deactivate workflow | 暂停所有路由 |

## 已知遗留（今天的切面）

1. **routeSpecDone 临时短路** — 当前 spec 完成直接 `mark_spec_reviewed`，跳过 ci-lint。
   - **原因**：ubox-crosser lint baseline + spec↔CI 反馈环导致下游链路被堵
   - **长期修**：spec issue 生命周期状态机（ci-launched / ci-passed / attempts），ci-lint 对 `openspec/**` diff scoped。
2. **dedup TTL=30min 是 hack** — 应改成 issue+event seq 的持久去重（workflowStaticData 重启会丢）。
3. **BKD 跨 session write scoping** — 今天清理 REQ-669 时 `dps25slr`/`nc5lu0uy` 被 BKD 拒绝（非本 session 创建的 issue 不能改 status）。需要用户手动收尾或在 BKD 里加白名单。
4. **accept 阶段未实现** — ci-integration pass 直接 escalate，`AI-QA agent + done_archive` 待接入。
5. **BUGFIX 链未实测** — Router + template 都就位，但 happy-path 从没触发过 bugfix（dev agent 每次一把过）。
6. **凭证硬编码** — Coder-Session-Token 在 workflow JSON 里裸串。
7. **父 issue 状态自动更新** — Done 节点暂时只 follow-up 当前验收 issue，父链不推 done（priorStatusId 闸够用就先不动）。

## 已废弃 / 已解决

- ~~v2 + bkd-events 双 workflow 拆分~~ → v3.1 单 workflow
- ~~84 节点 IF 长链~~ → Router Code 节点（可单测）
- ~~ANALYZE 重放触发重复 fan-out~~ → [FAN] Mark analyze done + priorStatusId 闸
- ~~spec → CI 反馈环（指数级生 issue）~~ → TEMP 短路 + dedup
- ~~title 参与调度~~ → 全走 tags
- ~~ci-runner 误改父 issue tag~~ → parent-id tag 方案 + prompt 强约束
- ~~dev Fu n8n extendSyntax 报 invalid syntax~~ → 用 `$json.reqId` 替代 `$node["..."]`
