# REQ-clone-fallback-direct-analyze-1777119520: fix(_clone): multi-layer involved_repos fallback for direct analyze entry

## 问题

REQ-clone-and-pr-ci-fallback-1777115925 让 `start_analyze` 在 dispatch
analyze-agent 之前 server-side clone `ctx.intake_finalized_intent.involved_repos`
（fallback `ctx.involved_repos`）到 `/workspace/source/<basename>/`。

但 `start_analyze` 有**两条入口**：

| 入口 | 触发 | ctx 内容 |
|---|---|---|
| **intake → analyze**  | `intent:intake` → 多轮 BKD chat → `INTAKE_PASS` → `start_analyze_with_finalized_intent` | webhook 解析 intake 最后一条 message 的 finalized intent JSON 写入 `ctx.intake_finalized_intent.involved_repos` |
| **直接 analyze**     | `intent:analyze` 一步到位 → `start_analyze` | webhook 只写 `{intent_issue_id, intent_title}`，**没有 involved_repos** |

直接 analyze 入口下 `_clone._resolve_repos` 一定返 `[]`，server-side clone 整段
跳过，sisyphus 把 clone 完全甩给 `analyze.md.j2` Part A.3 的"agent 自跑 helper"
fallback。本次（REQ-ups0uldr）就是这条路径，agent 必须自己 `kubectl exec
runner -- /opt/sisyphus/scripts/sisyphus-clone-repos.sh phona/sisyphus`。

后果（实证 2026-04-25 这个 session）：

- 单仓 self-dogfood（sisyphus 改 sisyphus）也得每次手 clone，prompt 那段"多数情况
  sisyphus 已替你跑"成了空头支票
- agent 的"自跑 helper"是软约束 —— 走 prompt 文字描述，没办法机制约束 agent 真跑
  （历史上有 analyze-agent 跳 Part A 直接 grep；REQ-checker-empty-source-1777113775
  增加了 checker 兜底，但仍是事后裁决）
- intent issue 上明明可以挂个 `repo:phona/sisyphus` tag 表明意图，**sisyphus 不读**
- 单仓部署（如 sisyphus 自身 dogfood、ttpos-server-go single-repo lab）应当能在
  ops 层配一次 default 仓 list，不该每次 intake

## 方案

把 `_clone._resolve_repos` 从 2 层升级到 4 层 fallback：

| 层 | 来源 | 触发场景 |
|---|---|---|
| L1 | `ctx.intake_finalized_intent.involved_repos` | intake → analyze 主路径（**不变**） |
| L2 | `ctx.involved_repos` | 老 caller / 测试夹具显式塞 ctx（**不变**） |
| L3 | BKD intent issue tags 形如 `repo:<org>/<name>` | 用户在直接 analyze 入口显式打 tag |
| L4 | `settings.default_involved_repos`（env `SISYPHUS_DEFAULT_INVOLVED_REPOS`） | 单仓 / 默认仓部署，ops 配一次 |

`start_analyze` 跟 `start_analyze_with_finalized_intent` 都改成传
`tags=tags, default_repos=settings.default_involved_repos` 给 helper；intake
路径会沿用 L1 命中，不受影响；直接 analyze 入口现在有 L3 / L4 兜底。

### 故意不做

- **不**从 `intent_title` / BKD prompt 自由文本 fuzzy parse `org/repo` slug。
  风险：路径串（`src/orchestrator`、`M14b/M14c`、`docs/integration-contracts.md`
  里的 `phona/foo` 示例）极易误命中 → 拉错仓污染 `/workspace/source/`，下游
  checker 行为不可预测。要文本声明就让用户显式打 `repo:` tag。
- **不**改 `analyze.md.j2`。模板已经按 `cloned_repos` 真假分支显示"sisyphus
  已替你 clone X"或"你必须自己 clone"，4 层全 miss 时仍 fall through 到原
  prompt 路径，agent 行为兼容。
- **不**给 L3 / L4 加 PR-CI 端的"discovery"。`pr_ci_watch` 已经从
  `/workspace/source/*` 实地探测，不依赖 ctx —— 这个 REQ 只动 analyze
  入口的 clone，pr_ci_watch 不动。

## 取舍

- **为什么 L3 是 `repo:` tag 而不是新 ctx 字段** —— BKD UI 没"自定义 ctx"
  入口；用户能改的就是 issue title / tags / chat。tag 是机器可读，prefix
  `repo:` 跟既有 `parent:` / `decision:` / `init:` / `target:` 几个 prefix
  保持一致风格，router/webhook 已经熟。
- **为什么 L4 default_factory 是 `[]` 而不是 `["phona/sisyphus"]`** —— 默认空，
  opt-in。多仓部署不能强加默认；单仓部署（sisyphus self-dogfood）通过 helm
  values `extraEnv: SISYPHUS_DEFAULT_INVOLVED_REPOS=phona/sisyphus` 显式配。
- **为什么 4 层而不是 5 层加 prompt body 解析** —— 见上方 "故意不做"。
- **为什么不用 AI 解析 intent_title 的 repo 提及** —— `_clone` 是机械 helper，
  调 LLM 解析自由文本让它脱离"薄编排"哲学；agent 自己有 prompt Part A.3 fallback
  路径，Last-resort 让 agent 决，比 sisyphus 假装智能强。
- **slug regex 严格度** —— 强 require `[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]*`，
  GitHub org / repo 命名规则的子集（org 最多 39 字符；repo 字符集允许 `._-`）。
  非法 slug **不**静默丢弃，log warning 让 ops 看到 typo。

## 影响面

- `orchestrator/src/orchestrator/actions/_clone.py`：
  - 重构 `_resolve_repos` → `resolve_repos`（公开 API，方便 unit + contract
    test 直接调）+ 加 4 层逻辑 + 返回 `(repos, source_label)`
  - 新增 helper `_extract_repo_tags(tags)` + `_REPO_SLUG_RE`
  - `clone_involved_repos_into_runner` 加 keyword-only `tags` + `default_repos`
    参数；旧签名 `(req_id, ctx)` 保持向后兼容（kwargs 可缺）
  - log 增加 `source` 字段（来自哪一层）方便观测
- `orchestrator/src/orchestrator/config.py`：加 `default_involved_repos: list[str] = Field(default_factory=list)`，env `SISYPHUS_DEFAULT_INVOLVED_REPOS`
- `orchestrator/src/orchestrator/actions/start_analyze.py`：调 `clone_involved_repos_into_runner` 时传 `tags=tags, default_repos=settings.default_involved_repos`
- `orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py`：同上（intake 路径正常用不上 L3/L4，但传进去保持调用形状一致 + 防 intake JSON 异常时降级到 L4）
- `orchestrator/tests/test_actions_start_analyze.py`：补 8 个 case 覆盖 4 层 priority + tag 解析 + start_analyze 透传
- `orchestrator/tests/test_contract_clone_fallback_direct_analyze.py`：新增。锁死
  - layer priority order
  - settings field 存在 + default `[]`
  - `_clone.py` 不准 import `intent_title` / `get_issue` / `description`（自由文本解析 guard）
  - start_analyze* 透传 `tags=tags` + `default_repos=settings.default_involved_repos`
  - slug 校验严格度

不动 / 不影响：

- `state.py` / `engine.py` —— 状态机不变
- `webhook.py` / `router.py` —— ctx 解析不变（webhook 仍只写 `intent_issue_id` + `intent_title`）
- `pr_ci_watch.py` / `staging_test.py` —— checker 路径不动
- `analyze.md.j2` —— 模板按 `cloned_repos` 真假分支已经处理两种情况，无需改
