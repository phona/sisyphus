# REQ-prompts-repo-agnostic-audit-1777189271: chore(prompts): audit + remove repo-specific bake-ins

## 问题

`orchestrator/src/orchestrator/prompts/` 是 sisyphus 喂给所有 BKD agent / verifier
agent 的 Jinja2 模板。sisyphus 自己是 **repo-agnostic 编排层**——按 CLAUDE.md
头条："薄编排，agent 决定"——本不该在 prompt 里钉死任何被编排仓的身份。

但现状审计发现**四类 repo-specific bake-in**散落在多个模板里，把 sisyphus
绑死在最初那个用户（phona / ttpos 产品线 / sisyphus 自身仓）上，
新接入仓的 agent 读到这些 example 时会被误导。

### 1. 占位 example 钉死 GitHub org `phona/`

10 处出现 `phona/repo-a` / `phona/repo-b` / `phona/<spec_home_repo>` 当占位例子，
而不是 `<owner>/repo-a` 这样的中性占位（`intake.md.j2:45` 已经用的就是
`owner/repo-a` 格式，证明这个写法在仓里早有先例）。

| 文件 | 行 | 内容 |
|---|---|---|
| `analyze.md.j2` | 106 | `phona/repo-a [phona/repo-b ...]` |
| `analyze.md.j2` | 120 | `gh repo clone phona/repo-a ./repo-a` |
| `analyze.md.j2` | 203 | `> spec home repo: phona/repo-a` |
| `done_archive.md.j2` | 85 | `"spec home repo: phona/xxx"` |
| `done_archive.md.j2` | 103-108 | `phona/repo-a#123` `phona/repo-b#456` 4 处 |
| `_shared/runner_container.md.j2` | 58 | `phona/repo-a phona/repo-b`（clone helper 例） |
| `verifier/dev_cross_check_fail.md.j2` | 18 | `（如 phona/repo-a）` |
| `verifier/spec_lint_fail.md.j2` | 20 | `（如 phona/repo-a）` |
| `verifier/_decision.md.j2` | 22 | `"target_repo": "phona/repo-a"` |
| `challenger.md.j2` | 136 | `gh repo clone phona/<spec_home_repo>` |

### 2. Makefile ci 契约被品牌化为 "ttpos-ci 标准"

`docs/integration-contracts.md` 把 source repo 必须提供的
`ci-lint / ci-unit-test / ci-integration-test` 一组 Makefile target 称作
"ttpos-ci 标准"——这是历史命名（最早出自 ttpos 产品仓）。但**契约本身**
跟 ttpos 无关，任何 source repo 接入 sisyphus 都得提供这套 target。
prompt 里 7 处 "ttpos-ci 标准" 让外人误以为必须先成为 ttpos 产品才能用 sisyphus。

| 文件 | 行 |
|---|---|
| `analyze.md.j2` | 65, 223 |
| `bugfix.md.j2` | 80 |
| `staging_test.md.j2` | 23 |
| `_shared/runner_container.md.j2` | 45 |
| `verifier/dev_cross_check_fail.md.j2` | 6 |
| `verifier/dev_cross_check_success.md.j2` | 6 |

### 3. acceptance scenario id 钉死 `FEATURE-A*` 前缀

`accept.md.j2` 让 accept-agent "找出所有 `#### Scenario: FEATURE-A*` block"，
verifier/accept_*.md.j2 也跟进。但 scenario id 是 spec 作者定的，sisyphus 不该
预设前缀。`analyze.md.j2:45` 自己的 spec example 用的就是 `UBOX-S1` 不是
`FEATURE-A1`——前缀本来就该跟 spec 走，accept 阶段用 `#### Scenario:` 开头匹配
就够了（这也是 `check-scenario-refs.sh` 的匹配模式）。

| 文件 | 行 | 内容 |
|---|---|---|
| `accept.md.j2` | 28 | `跑 FEATURE-A* Acceptance Scenario` |
| `accept.md.j2` | 58 | `所有 \`#### Scenario: FEATURE-A*\` block` |
| `accept.md.j2` | 79-80 | `FEATURE-A1: PASS / FEATURE-A2: FAIL` 报告样例 |
| `verifier/accept_fail.md.j2` | 4 | `accept-agent 跑 FEATURE-A* 时...` |
| `verifier/accept_success.md.j2` | 4, 10 | 两处 `FEATURE-A*` |

### 4. 钉死 source repo basename `sisyphus`

`bugfix.md.j2:62` 在 Step 1 拉 dev-fix 工作树时硬编码：

```bash
kubectl ... -- bash -c "cd /workspace/source/sisyphus && git fetch ..."
```

而同一文件 Step 3 / 3.5 / 4（line 75/87/103）已经用 `/workspace/source/REPO`
占位。Step 1 是被遗漏的回归——只有当 dev-fixer 修的就是 sisyphus 仓自己
（M0 自举测试场景）才能跑通；外仓 REQ 跑这条会 `cd: no such file`。

另外 `challenger.md.j2:36, 150` 用 `/workspace/source/<spec_home_repo>/`，但
`<spec_home_repo>` 占位的是 `<owner>/<repo>`（见 done_archive.md.j2:85 example
`phona/xxx`）；clone helper 落到 `/workspace/source/<basename>/`（见
`scripts/sisyphus-clone-repos.sh`），所以 challenger 的路径形式实际不存在
（会得到 `/workspace/source/owner/repo/openspec/...`）。这是 spec_home 占位语
义不一致引发的隐性 bug，本 REQ 一并修。

## 方案

**只动 `orchestrator/src/orchestrator/prompts/` 里的模板内容**，不动 Python 代码、
checker、state machine、docs、deployments。原则：

1. **占位 example 中性化**：把 `phona/repo-a` / `phona/repo-b` 一律改成
   `<owner>/repo-a` / `<owner>/repo-b`，把 `phona/<spec_home_repo>` 中
   `phona/` 前缀去掉（占位本身已是 `<owner>/<repo>` 形式）。`phona/xxx` 同。
2. **去品牌化 Makefile 契约**：把 "ttpos-ci 标准" 一律改成 "Makefile ci 契约"
   并附加 `（详见 docs/integration-contracts.md）` 链接，让指针指到真契约
   而非品牌名。
3. **acceptance scenario 通用化**：去掉 `FEATURE-A*` 前缀假设。改成 "spec 里
   定义的 Acceptance Scenario block（`#### Scenario:` 开头）"，example 报告里
   把 `FEATURE-A1` 换成中性的 `<scenario-id>` 或 `S1`。
4. **修 sisyphus basename 硬码**：`bugfix.md.j2:62` 把 `sisyphus` 改成 `REPO`
   占位（与同文件 Step 3+ 一致）。`challenger.md.j2:36, 150` 的
   `<spec_home_repo>` 在路径里换成 `<spec_home_repo_basename>` 占位（注释里
   解释一句 "basename = github 仓名最后一段，与 sisyphus-clone-repos.sh 行为一致"）。

## 取舍

- **为什么不顺手清 docs/ 里的 `ttpos-ci 标准`** —— 范围隔离原则：本 REQ 题目
  是 prompt 审计，不是 docs 审计。docs/integration-contracts.md 的 "ttpos-ci 标准"
  是契约文档**自身的命名**，要不要重命名是另一个 REQ（涉及更多 doc 互引、
  外部链接、git 历史检索关键词）。prompt 改完后让模板指 docs 链接就够了。
- **为什么不去掉 `## 过去留下的废契约` 里 `leader_repo_path` 那条** ——
  那是显式 "不要再做" 提示，对老 agent / 老接入文档**有用**（防回归）。
  词汇本身在那里只起反面 example 作用，不算 bake-in。
- **为什么不动 `staging_test.md.j2` 里的 `mcp__bkd__follow-up-issue` 引用** ——
  这是 LEGACY 路径（文件头明确标 `[LEGACY BKD-agent path]`，flag=False 才走），
  且不是 "repo-specific" 而是 "tool-whitelist 不一致" 的另一类问题。范围隔离。
- **为什么不引入 prompt 自动审计 hook（CI 拦 `phona/` etc.）** —— 加
  机械防回归是另一类工作（需要白名单、誤判处理、CI 接入）。本 REQ 先把存量
  清掉；防回归留给 spec 中 scenario 描述的 grep 不变量提示，将来要做 hook 直接
  用 spec 的 ID 列表当种子。
- **为什么 `<owner>` 而不是 `phona` 替换为 `acme` 或别的中性 org**——
  `<owner>` 是占位语义（明确告诉读者"这里填你的 org"），任何具象 org
  名（acme / example）都仍是 example 钉死，新接入还要再做 mental
  substitution。`intake.md.j2:45` 已用 `owner/repo-a` 风格，保持一致。

## 影响面

- 改 11 个 prompt 模板（见上表汇总），文本替换；**不改任何 Python 代码、
  Jinja2 渲染上下文 (router.py / engine.py / actions/)、checker、helm chart、
  migrations**。
- 不改 BKD REST 调用契约、tool whitelist、状态机 transition、orchestrator HTTP API。
- 渲染出的 prompt 里 example 仓名变成 `<owner>/repo-a`——下一个 BKD agent 拿到
  prompt 时看到的占位语义更明显，**不影响任何运行时行为**（agent 本来就会
  从 BKD intent issue description 里读真实仓列表，example 只是教学用）。
- 不改 `docs/integration-contracts.md` 里 "ttpos-ci 标准" 命名；但 prompt 中所有
  指向该契约的链接将变得指向 `docs/integration-contracts.md` 而非品牌词。
- 测试 / accept 不需要新增——验证手段是后续 spec_lint / dev_cross_check / 灰盒
  grep（spec.md 里的 scenario 列出的不变量）。
