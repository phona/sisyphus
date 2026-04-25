# REQ-clone-and-pr-ci-fallback-1777115925: fix(start_analyze + pr_ci_watch): server-side clone + refuse stale env fallback

## 问题

两条相互啮合的形状缺陷今天还撑着 sisyphus 的 happy path：

### 缺陷 A：start_analyze 不做 server-side clone，agent 拿空 PVC 起跳

`actions/start_analyze.py` 跟 `actions/start_analyze_with_finalized_intent.py`
都只 `ensure_runner` 拉 Pod + PVC，然后立刻 follow-up `analyze.md.j2` prompt
让 agent 自己 `kubectl exec ... /opt/sisyphus/scripts/sisyphus-clone-repos.sh ...`。
prompt Part A.3 / A.4 把 clone 整套责任全压给 agent。

后果：

- intake 阶段已经在 `ctx.intake_finalized_intent.involved_repos` 落了
  ground-truth 仓列表，sisyphus 拥有这条信号但**主动忽略**它。
- 进 analyze stage 时 `/workspace/source/` 是空的（PVC 新挂或上一 REQ 被 GC）。
  agent 必须先做 6 行 prompt 仪式才进入"实际写 spec / 写代码"——这 6 行
  里随便错一行（kubectl exec 命令拼错 / 仓名打字 / shallow refspec 漏带）
  整个 stage 都得回头修。
- M15 之后 sisyphus 不再起 spec / dev 子 agent，analyze-agent 全责交付。
  让全责 agent 仍要跑半段编排活，分工边界糊。
- 真出过事故：agent kubectl exec clone 命令漏拼 `https://`、agent 误 clone
  到 `/workspace/<repo>/` 而非 `/workspace/source/<repo>/`，
  spec_lint / dev_cross_check 都因为找不到 `/workspace/source/*/` 直接
  silent-pass（直到 REQ-checker-empty-source-1777113775 才硬关掉这条
  silent-pass）。**根因是 clone 这步不该在 agent 里。**

### 缺陷 B：pr_ci_watch checker 在缺仓时偷偷读 SISYPHUS_BUSINESS_REPO env

`checkers/pr_ci_watch.py:86`：

```python
repo_list = repos or ([os.getenv("SISYPHUS_BUSINESS_REPO")] if os.getenv("SISYPHUS_BUSINESS_REPO") else [])
```

`actions/create_pr_ci_watch.py:78-81` 决定不了 repo 时（runner 文件系统空 +
ctx 没 finalized intent + ctx 没 involved_repos）传 `repos=None` 给 checker，
checker 静默 fallback 到全局 env var。

`SISYPHUS_BUSINESS_REPO` 是 M15 之前的单仓 manifest 残骸，**全局共享**：

- orchestrator deployment 启动时一次性设，所有 REQ 共用同一个值
- 多仓 REQ 上下来时它只能指其中一个仓，剩下几个仓的 PR check-runs 一律
  漏看
- 旧单仓 REQ archive 之后这个 env 还在，新 REQ 被 fallback 到老仓 ——
  pr_ci_watch 拿过期上下文判 PR check-runs，结果可能：
  - 新 REQ 在仓 X，env 指仓 Y → 永远找不到 PR → exit 1（误 fail，触发
    verifier-agent → fix-dev → fixer-agent 兜，浪费多轮）
  - 新 REQ 在仓 Y（巧合命中 env）→ 看到的是真 PR，但只检了 1 个仓，多仓
    REQ 的别的仓全跳过 → 假阳性 pass，状态机推到 accept 才被人肉 catch

**stale env fallback 是 silent-pass 的同形错误**：在没收到 per-REQ 信号
（runner 没 clone、ctx 没声明）时不该编一个，应当显式失败让 verifier 兜。

## 方案

A 改 push，B 改 refuse；两条独立但互补。

### Fix A：start_analyze 接管 clone（server-side，不再委托 agent）

`start_analyze` / `start_analyze_with_finalized_intent` 在 `ensure_runner`
返 ready 后、`bkd.follow_up_issue(prompt)` 之前，**多一步 server-side
clone**：

1. 从 ctx 解出 repos（优先级：`ctx.intake_finalized_intent.involved_repos`
   → `ctx.involved_repos`）
2. 有 repos：通过 `k8s_runner.exec_in_runner` 跑
   `/opt/sisyphus/scripts/sisyphus-clone-repos.sh <r1> <r2> ...`
3. 退码非 0 → log + emit `VERIFY_ESCALATE`（不打 analyze-agent；clone 起跳
   失败时 agent 也救不动）
4. 退码 0 / 没 repos：继续往下 dispatch agent

新 prompt 由 sisyphus 在 follow-up 里附一句 "/workspace/source/* 已经预
clone 好"（analyze.md.j2 的 Part A.3 改成"如果 sisyphus 没替你 clone，再调
helper"，agent 跳过 clone 直接进 spec / dev）。

### Fix B：pr_ci_watch 拒绝 env fallback

`checkers/pr_ci_watch.py:watch_pr_ci`：

```python
# 旧：repo_list = repos or [os.getenv("SISYPHUS_BUSINESS_REPO")] if env-set else []
# 新：repo_list = list(repos or [])；空 → ValueError "no repos provided"
```

action layer `create_pr_ci_watch._run_checker` 的 source 序列保留：

1. runner 文件系统 discovery（fix A 后 server-side clone 的 ground truth）
2. `ctx.intake_finalized_intent.involved_repos`
3. `ctx.involved_repos`

三个都空 → `repos=None` 传给 checker → ValueError → action 把它当
config error 翻译为 `PR_CI_TIMEOUT`（直接 ESCALATED，不进 verifier
review）。

`SISYPHUS_BUSINESS_REPO` 这个 env 名字本身**全删**：从配置层
（`config.py`）和 helm values 里也清掉，避免后人再 fallback。

### 配套：analyze.md.j2 prompt 调整

Part A.3 (clone) 从 "agent 必须跑" 改成 "sisyphus 已替你跑；万一 ctx 没传
repos sisyphus 跳过了，再手跑 helper"。Part A.2 (决定哪些仓) 在 intake
路径上失效（intake 阶段已经声明），保留为直接 analyze 路径的 fallback。

## 取舍

- **为什么 server-side clone 出错就 ESCALATED 而非 retry 一次** —— clone
  失败的根因有限：(i) GitHub 凭证过期 / 仓名打错（agent retry 也救不了），
  (ii) 网络抖动（runner pod 重 schedule 后会重试一次，sisyphus 状态机
  retry policy 兜底）。**配置错落** retry 没用，让人翻 escalate 队列才是
  正确路径。
- **为什么不改成"不传 repos = clone 全部 PR-CI-historic-repo"** —— 全局
  默认仓集合就是 SISYPHUS_BUSINESS_REPO 的同形错误，再做一遍。直接路径
  没 intake 时就让 user 在 BKD intent issue 里贴 finalized intent JSON
  （或者补加一条 `intent:analyze:involved_repos:owner/repo` tag 给 router
  拾），是清晰的扩展点。**本 REQ 不实现这条扩展**，只把 fallback 路径全删。
- **删 env var 会破老单仓 REQ 么** —— `SISYPHUS_BUSINESS_REPO` 这个 env
  在 sisyphus 仓里只被 `pr_ci_watch.py` 读。Helm values / dev 文档里出现
  几处但都是注释 / docs；运行时只此一处。删干净。
- **start_analyze 直接路径（无 intake）怎么办** —— ctx 里没
  `intake_finalized_intent`，也没 `involved_repos`，sisyphus 跳过
  server-side clone 那一步，**保留** agent 自己在 prompt 里 clone 的老
  路径（Part A.3）。这是过渡；下一个 REQ 可以加直接路径的 repo 声明协议。
- **agent prompt 同时介绍两套路径会乱么** —— 不会。prompt 改成"sisyphus
  已经预 clone（多数情况 = intake 走完）；如果 `/workspace/source` 是空，
  你自己 helper clone"。一个 `[ -d /workspace/source/*/ ] || /opt/sisyphus/scripts/sisyphus-clone-repos.sh ...`
  guard 解决。

## 影响面

- 改 `actions/start_analyze.py` / `actions/start_analyze_with_finalized_intent.py`：
  在 ensure_runner 之后加 server-side clone（条件触发）
- 改 `checkers/pr_ci_watch.py`：删 env fallback，empty `repos=None` 直接 ValueError
- 改 `actions/create_pr_ci_watch.py` 注释：删第 12 行的"3. SISYPHUS_BUSINESS_REPO env"档
- 改 `prompts/analyze.md.j2`：Part A.3 从硬要求改成 fallback；强调 ctx 已 clone
- 改 `prompts/_shared/runner_container.md.j2`（若提到 clone 则同步）
- 测试：`test_checkers_pr_ci_watch.py` 现有 13 个 case 中 9 个用 env，改成
  显式 `repos=` 参数；新增 `test_watch_pr_ci_ignores_env_var_when_repos_none`
  验证 env 即使设了 repos=None 也 ValueError。
- 新增 `test_actions_start_analyze.py`：覆盖 server-side clone 派发、
  clone 失败 → VERIFY_ESCALATE、无 involved_repos → 跳过 clone。
- 不动 `state.py` / `event_log` 表结构 / Postgres migrations。
- `SISYPHUS_BUSINESS_REPO` env 从 helm values（如果有引用）和 README 里删。
