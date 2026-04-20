# Sisyphus Router (n8n Code-node 路由)

把 v3-events 里散成 ~60 个 IF/Set 节点的路由逻辑收敛成一个 Code 节点里的纯函数 bundle。本地可 `node --test` 直接验证，n8n 里靠 build 脚本注入。

## 为什么这样做

- **节点数**：v3-events 原本 84 节点，v3.1 目标 ~16 节点（路由逻辑全收进 Router Code 节点）
- **可测试性**：`router.js` 是纯函数（输入 webhook body → 输出 `{action, params, reason}`），本地就能 `node --test` 跑，不用动 n8n
- **可读 diff**：改路由规则改 .js 文件，git diff 正常；n8n UI 不用反序列化 jsCode 字符串
- **可视化保留**：n8n 里还是能看 webhook 进来、Router 算了啥、Switch 分发、每个 action 的 execution 历史

## 目录

```
router/
├── router.js              # 路由决策纯函数（routeEvent, parseTags, expectedSpecsFor）
├── ci-diagnose.js         # CI 失败诊断分类 + CI Result block 解析
├── router.test.js         # 36 个单测（node --test）
├── ci-diagnose.test.js    # 6 个单测
├── bundle.test.js         # 9 个单测：验证 build 脚本产物能跑
└── package.json
```

构建产物：

```
scripts/build-workflow.js                       # 把 router/*.js 注入 Code 节点 jsCode 字段
charts/n8n-workflows/v3.1/
  ├── v3-events.template.json                   # 骨架（Webhook + Router + Switch + stub actions）
  └── v3-events.json                            # 构建产物，可直接 import 到 n8n
```

## 开发循环

```bash
# 改 router/router.js 或 router/ci-diagnose.js
cd router && node --test router.test.js ci-diagnose.test.js bundle.test.js
# ↑ 全绿

# 重新构建 workflow JSON
cd .. && node scripts/build-workflow.js
# ↑ 会重写 charts/n8n-workflows/v3.1/v3-events.json

# 在 n8n UI 里 re-import（或用 kubectl cp + n8n import:workflow CLI）
```

## 路由契约

`routeEvent(webhookBody)` 返回 `{action, params, reason?}`，下游 Switch 节点按 `action` 字段分发。可能的 action：

| action | 触发条件 | 下游 stub（v3.1）要做的事 |
|---|---|---|
| `skip` | priorStatusId=done / spec 无 specStage | noop |
| `create_ci_runner` | dev 或 \*-spec 完成 | BKD `create-issue` 建 CI 核验 issue，tags=[ci, REQ-xx, target:{target}], params 含 branch / parentIssueId / parentStage |
| `comment_back` | ci-runner 轻量失败（lint/unit）| BKD `follow-up-issue` 在 parentIssueId 上加评论 + 状态回 in_progress |
| `create_bugfix` | ci:fail+target:integration / accept fail | BKD `create-issue` 建 bugfix-dev issue，tags=[bugfix, REQ-xx, round-N] |
| `create_test_fix` | bugfix 完成（非 spec-bug） | BKD `create-issue` 建 test-fix issue |
| `create_reviewer` | test-fix 完成 | BKD `create-issue` 建 reviewer issue |
| `proceed_verify` | ci:pass(unit) / reviewer pass | BKD `create-issue` 建 verify issue（或直接建 ci(integration)）|
| `proceed_accept` | ci:pass(integration) / verify pass（legacy） | BKD `create-issue` 建 accept issue |
| `fanout_specs` | analyze 完成带 layers | BKD `create-issue` × N，按 params.specs 建对应 spec issue |
| `mark_spec_reviewed` | ci:pass(lint) on spec | BKD `update-issue` 加 review tag，触发 Spec Gate 汇合 |
| `done_archive` | accept pass | `openspec apply` + `gh pr create` |
| `escalate` | 各种异常路径 | BKD `update-issue` 加 `escalation` tag + Lark 通知 |

## 待办

v3.1 的 action 节点当前全是 noOp stub。实际接 BKD 要做：

- [ ] stub → 真 BKD MCP HTTP 调用（参考 v3-events.json 的 Init MCP + Create Issue 节点）
- [ ] `mark_spec_reviewed` 后的 Spec Gate 汇合逻辑（当前 v3-events 用 AllSpecsReview 子链实现，v3.1 可以收进 Router 里算）
- [ ] `done_archive` 的多步骤（openspec apply / git push / gh pr create）可能要 sub-workflow
- [ ] v3.1 部署并切流量（先 shadow 跑一段时间对比 v3 behaviour）
- [ ] n8n 里真·活体 smoke：刚才尝试 SFTP deploy 失败了，后续手动 UI import 一次验证

## 已知限制

- **n8n Code 节点 sandbox 禁用了** `process` / `console.log` / `require` / Node builtins — router 代码里不要用
- **luxon DateTime 可用**，lodash `_` 默认不可用（想用要开 `NODE_FUNCTION_ALLOW_EXTERNAL`）
- 路由出错会被外层 try/catch 捕获 → 降级为 `{action: 'escalate', reason: 'router_exception'}`
