# BKD issue tag 命名规范（router.py 依赖）

> **唯一真相源**：[orchestrator/src/orchestrator/router.py](../orchestrator/src/orchestrator/router.py)。
> 本文档记录 sisyphus router 实际识别的 tag 模式，方便 agent / 接入方 / 维护者
> 对照写入。**router 只看 tag 不看 title**，所以 tag 是 sisyphus 跟 BKD agent 沟通
> 的唯一信道。

router 的入口是 [`derive_event(event_type, tags)`](../orchestrator/src/orchestrator/router.py)，
按事件类型（`issue.updated` / `session.completed` / `session.failed`）+ tag 集合
推断 sisyphus state-machine 的 `Event`。

## 1. 入口 tag —— 启动 REQ

人在 BKD UI 给 issue 打这两类 tag 之一来触发流水线：

| tag | 触发 Event | 意义 |
|---|---|---|
| `intent:intake` | `INTENT_INTAKE` | 走完整流水线：先起 intake-agent 多轮澄清需求 → finalized intent JSON → 新建 analyze issue 接力（推荐：不熟悉的仓） |
| `intent:analyze` | `INTENT_ANALYZE` | 跳过 intake，直接进 analyze-agent（适合 trivial REQ） |

router 看到 `intent:*` tag 且 issue 还没进 `analyze` / `intake` 阶段（即 issue 上**没有**
对应的 stage role tag）才 fire，避免 self-loop。

## 2. REQ 标识 tag

| 形式 | 例 | 谁写 | 谁读 |
|---|---|---|---|
| `REQ-<slug>` | `REQ-docs-drift-audit-1777220568` | 人或上游创建 issue 时带 | router `extract_req_id`，找不到时退回 `REQ-<issueNumber>` |

REQ id 是 workflow 标识；一条 BKD issue 同时有 BKD 行 id（`fepr3eys` 这种）和 REQ id
两个标识，**不要混淆**。REQ id 是 tag，BKD 行 id 是 REST endpoint 的 `{id}`。

## 3. Stage role tag —— 谁在跑

每个 stage agent / sub-agent 在 BKD issue 上必带的 role tag。router 用它分流
`session.completed` 事件到对应主链 transition：

| tag | 用在哪 |
|---|---|
| `intake` | intake-agent issue |
| `analyze` | analyze-agent issue（M17 全责交付） |
| `challenger` | challenger-agent issue（M18） |
| `staging-test` | （v0.1 兼容）staging-test agent issue；M1 起被 checker 取代但 tag 仍走 router |
| `pr-ci` | （v0.1 兼容）pr-ci-watch agent issue；M2 起被 checker 取代 |
| `accept` | accept-agent issue |
| `verifier` | verifier-agent issue |
| `fixer` | fixer-agent issue |
| `done-archive` | done-archive agent issue |

## 4. 结果 tag —— 报告 stage 完成

stage agent 完成时 PATCH 自己的 issue 加这些 tag，router 据此 fire 对应 `*_PASS` /
`*_FAIL` 事件。**注意 tag 替换语义**：每次 PATCH 必须先 GET 当前 tags，merge 后再 PATCH。

| tag | 含义 |
|---|---|
| `result:pass` | 该 stage 跑通（pass / 通过 / 全绿） |
| `result:fail` | 该 stage 失败 |

router 看到 `result:*` 时配合 stage role tag 推断具体事件，例如：
- `intake` + `result:pass` → `INTAKE_PASS`
- `challenger` + `result:fail` → `CHALLENGER_FAIL`
- `accept` + `result:pass` → `ACCEPT_PASS`
- `done-archive` + `result:pass` → `ARCHIVE_DONE`

`pr-ci` 类比但用 `pr-ci:pass` / `pr-ci:fail` / `pr-ci:timeout` 子键空间（见 router.py L302–310）。

## 5. verifier-agent 专用 tag

verifier-agent 完成 session 时必加：

| tag | 含义 |
|---|---|
| `verifier` | role tag |
| `verify:<stage>` | 这次 verify 的目标 stage（`analyze` / `spec_lint` / `challenger` / `dev_cross_check` / `staging_test` / `pr_ci` / `accept`） |
| `trigger:<success\|fail>` | 触发本次 verify 的事件类型（success = stage 机械 pass 后的 sanity verify；fail = stage 机械 fail） |
| `decision:<urlsafe-base64-json>` | 决策 JSON（首选；router 看 tag 优先，base64 编码避开 BKD tag 字符限制） |

decision JSON schema（router `validate_decision`）：

```json
{
  "action": "pass" | "fix" | "escalate",
  "fixer": "dev" | "spec" | null,
  "confidence": "high" | "low",
  "reason": "≤ 500 字解释",
  "target_repo": "owner/repo  (M16 多仓可选，告诉 fixer 哪仓修)"
}
```

约束：
- `action=fix` 时 `fixer` 必须非 null；其他 action `fixer` 必须 null。
- 不合规 → `VERIFY_ESCALATE` → 终态 ESCALATED。

兜底：decision JSON 也可以写在 issue description 的 ```` ```json ```` 块里（取最后一个），
router `extract_decision_from_issue` 优先 tag、其次 description code block、最后扫
description 大括号。

## 6. fixer-agent 专用 tag

| tag | 含义 |
|---|---|
| `fixer` | role tag |
| `fixer:dev` 或 `fixer:spec` | scope（业务码 vs spec） |
| `parent-stage:<stage>` | fixer 是修哪个 stage 的失败（用于关联 stage_runs） |
| `parent-id:<verifier_issue_id>` | 关联到判 fix 的 verifier issue（router `get_parent_id`） |
| `round-N` | 第几轮 fixer（router `get_round`，用于 fixer round cap） |

## 7. M16 多仓辅助 tag（可选）

| tag | 含义 |
|---|---|
| `repo:<owner/repo>` | 当前 sub-issue / verifier 决策针对哪个仓（多仓 REQ 时可选；router `get_target` 也支持 `target:<repo>` 别名） |
| `parent:<stage>` | 通用 parent stage 标记（区别于 `parent-id:`） |

## 8. 不要做的事

- ❌ 不要在 PATCH tags 时只传新 tag，导致 REQ-xxx / role tag 被覆盖丢失
  （BKD 的 tags 字段是整数组替换语义，必须先 GET 再 merge）
- ❌ 不要给 fixer 的 issue 漏 `round-N`：fixer round cap 靠它判断
- ❌ 不要把 decision JSON 直接拼进 tag 文本（不 base64）—— BKD tag 字符集限制会丢字符
- ❌ 不要给 sub-agent 加 `intent:*` —— 那是人触发入口，agent 内部派 sub-issue
  请用 `parent-id:` + role tag

## 9. 加新 tag / 新 stage 时

1. 在 [router.py `derive_event`](../orchestrator/src/orchestrator/router.py) 加 tag → Event 翻译
2. 在 [state.py](../orchestrator/src/orchestrator/state.py) 加对应 `Event` / `ReqState` / `Transition`
3. 给该 stage 的 agent prompt（`prompts/<stage>.md.j2`）加上"完成时 PATCH 这些 tag"指令
4. 如果该 stage 走 verifier 框架，加 `prompts/verifier/<stage>_{success,fail}.md.j2`
5. 更新本文档表格 + [docs/state-machine.md](./state-machine.md) §3 Event 表
