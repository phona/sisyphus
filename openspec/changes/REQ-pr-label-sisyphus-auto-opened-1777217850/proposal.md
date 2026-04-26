# REQ-pr-label-sisyphus-auto-opened-1777217850: feat(prompts): label sisyphus-opened PR/issue auto

## 问题

sisyphus pipeline 会自动开两类对外可见的工件：

1. **GitHub Pull Requests** —— 由 analyze-agent（以及它派生的 sub-agent）在 dev 阶段
   通过 `gh pr create` 推到每个被改的 source repo。
2. **BKD issues** —— 既由 orchestrator 程序通过 `bkd.create_issue` 自动开
   （analyze / staging-test / pr-ci-watch / accept / done-archive / verifier / fixer /
   challenger 八个角色），也由 analyze-agent 自己 fan-out 时通过
   `curl POST /api/projects/{alias}/issues` 开 sub-issue。

现状这些 PR / issue **没有任何统一标识表明它们来自 sisyphus**。
人在 GitHub 列表 / BKD 看板里看到一条要点开 title 才知道是 bot 开的还是人开的；
Metabase 看板（M7 / M14e）想筛"sisyphus pipeline 自己跑出来的工件"也没法用 SQL
直接 `WHERE label = 'sisyphus'` —— 必须扫 title 前缀正则、容易漏。

> CLAUDE.md 头条的 "薄编排，agent 决定"：sisyphus 只出 mechanical 标签 + 不夺
> agent 的判断权。`sisyphus` 这个统一身份标签**不影响**任何 agent 决策（agent 不
> 看自己 issue 的 tags），它纯粹给**人 + 仪表盘**用。

## 方案

引入一个统一的"开源标签" `sisyphus`，**自动**贴到每一个 sisyphus pipeline 开
出来的 BKD issue 和 GitHub PR 上。

### 1. orchestrator 程序创建 BKD issue：在 BKD 客户端层强制注入

修改 `orchestrator/src/orchestrator/bkd_rest.py` `BKDRestClient.create_issue`：
若入参 `tags` 不含 `"sisyphus"` 则**前置插入**到列表头部（保持调用方传入顺序
作为后段）。同样改 `bkd_mcp.py` `BKDMcpClient.create_issue` 保持 transport 一致。

**为什么放客户端层而不是每个 callsite**：sisyphus 现有 8 处
`bkd.create_issue` 调用（actions/start_analyze_with_finalized_intent.py,
actions/create_staging_test.py, actions/create_pr_ci_watch.py,
actions/create_accept.py, actions/done_archive.py,
actions/_verifier.py ×2, actions/start_challenger.py），将来还会再加。客户端层
注入 = 一次写、所有现存 / 未来 callsite 自动继承，杜绝"漏标"回归。

`actions/start_intake.py` 是唯一不走 `create_issue`（intent issue 由 user 在
BKD UI 手动开，sisyphus 只 `update_issue` 改 title + tags）的特例 —— 显式
在 `tags=[..., "sisyphus"]` 里加上即可。

### 2. analyze-agent 开 PR / 开 sub-issue：在 prompt 里硬性要求

`orchestrator/src/orchestrator/prompts/analyze.md.j2` 新增一节
`Stage: PR sisyphus 标识`：

- **GitHub PR**：开 PR 前必须先 `gh label create sisyphus --color "6E5494" --description
  "Opened by sisyphus pipeline" --force`（idempotent —— 已存在则覆盖颜色 / 描述，
  不存在则创建），随后 `gh pr create --label sisyphus ...`。
- **BKD sub-issue fan-out**：`curl POST /api/projects/{alias}/issues` 的 `tags`
  字段必须包含字符串 `"sisyphus"`。

`orchestrator/src/orchestrator/prompts/_shared/tools_whitelist.md.j2` 里的 curl
POST sub-issue 例子也改成包含 `"sisyphus"`，避免 agent 复制粘贴漏带。

### 3. 不动的事

- **不**在 GitHub repo 仓里预创建 label —— 由 agent 第一次开 PR 时
  `gh label create --force` 兜底（label 在 repo 维度，不预设也不阻塞）。
- **不**改 done_archive.md.j2 / accept.md.j2 / challenger.md.j2 / bugfix.md.j2 ——
  这些角色不开 PR、不创建 sub-issue（fixer 直接 commit/push 到既有 feat 分支，
  challenger 同样 push 既有分支）。
- **不**改 router / state machine / migrations / dashboards SQL —— 标签是写入
  端的事，下游过滤用普通 SQL 即可。
- **不**做"反向校验"（比如 webhook 拒收没 sisyphus 标签的"bot 开"PR）——
  本 REQ 是出口侧契约，进口侧防伪是另一类工作（也不必要：sisyphus 的 trust
  boundary 是它自己开的工件，外部看 label 用即可）。

## 取舍

- **为什么是 `sisyphus` 而不是 `bot` / `automation`** —— `sisyphus` 是项目自身名字、
  跟其他通用 bot 标签（`renovate` / `dependabot`）平级，避免新接入仓里跟既存
  自动化标签撞车。颜色 `#6E5494`（紫色）也避开常见 GitHub 默认 label 色号。
- **为什么客户端层注入而不是 actions/* 层注入** —— actions 8 处分散，加 sub-agent
  / 新 stage 时容易漏；BKDRestClient.create_issue 是窄入口，注入逻辑两三行、
  幂等（已含 sisyphus 不重复加），是单一变更点。
- **为什么 PR label 是 prompt 强约束而不是 webhook 兜底** —— sisyphus 不在
  PR 创建路径上（agent 在 runner pod 内 `gh pr create`，sisyphus 拿不到事件），
  没法在创建后追加。webhook 监听 `pull_request.opened` 加 label 在理论上可以，
  但需要 sisyphus 提一个新 GitHub App / webhook 端口、引入新失败模式 —— 而 prompt
  约束 + verifier audit 已经够。
- **为什么 intake 阶段的 intent issue 也要标 sisyphus** —— intent issue 由 user
  手动开（确实不是 sisyphus 创造的实体），但 `start_intake` 在收到事件后会
  PATCH 改 tags 把它"接管"为 sisyphus 工作流的一部分；从 user 视角这条 issue
  从那一刻起也由 sisyphus 编排，所以一并标注，跟 BKD 看板筛选语义保持一致。

## 影响面

- 改 2 个 Python 模块（`bkd_rest.py` / `bkd_mcp.py`）+ 1 个 action
  （`start_intake.py`）+ 2 个 Jinja2 prompt（`analyze.md.j2` /
  `_shared/tools_whitelist.md.j2`）。不改 router / state / engine / checker /
  helm chart / migrations。
- 测试：`tests/test_bkd_rest.py::test_create_issue_payload_shape` 当前断言
  `tags == ["intent:analyze", "REQ-1"]`，需要更新为预期前置 `"sisyphus"`。
  新增 4 条单测覆盖：注入 + 已含时不重复 + intake 路径含 sisyphus + 渲染后
  prompt 含 `--label sisyphus` 与 `gh label create sisyphus`。
- 不改 BKD REST 调用 contract / tool whitelist 范围。
- 渲染出的 prompt 里 example curl POST 多带一个 tag，sub-agent 复制后
  自动跟着标。
- 已开过的存量 BKD issue / 已合并的 PR **不会**被回填打标 —— 只对本变更上线后
  新创建的工件生效。这是预期：本 REQ 是 forward-only。
