# REQ-issue-link-pr-quality-base-1777218242: feat: every sisyphus issue must link the REQ PR

## Why

为一条 REQ，sisyphus 在 BKD 里会创建一连串 issue：analyze（intent issue 改名）、
staging-test（legacy path）、pr-ci-watch（legacy path）、accept、done-archive、
challenger、verifier、fixer。人在 BKD UI 看 sub-issue 时，**没有任何线索**指向
GitHub 上跑着的实际 PR —— 必须翻 ctx / open verifier / 看 prompt 渲染才能挖出
PR 号。

这是 quality-base 类的体感问题：每条 sisyphus 创的 BKD issue 都应该带 `pr:<owner>/<repo>#<N>`
tag（一个 PR 一个 tag，多仓 REQ 多个 tag），让人在 UI 里"一跳到 PR"。

## What Changes

新增一个轻 helper `pr_links` + 在每个 sisyphus 创 BKD issue 的 callsite 注入
PR-link tag。

```
pr_links.ensure_pr_links_in_ctx(req_id, branch, ctx)
  ↓
1. ctx.pr_links 已缓存？→ 直接返回（O(1)）
2. 缓存 miss：
   - runner pod 里 ls /workspace/source/*/ 拿仓清单（复用 create_pr_ci_watch 的同款 helper 逻辑）
   - 每个仓 GET /repos/{repo}/pulls?head={owner}:{branch}&state=open 取 PR
   - 失败 best-effort：log warning，返空 list（**不**阻断 issue 创建）
   - 成功 → ctx.pr_links = [{repo, number, url}, ...]，update_context 落 jsonb
   - 第一次成功后顺手回填 ctx 已知 sisyphus issue id 的 tag（典型场景：analyze
     issue 在创建时 PR 还不存在 → 第一次 verifier 创建时回填 analyze issue tag）

pr_links.pr_link_tags(links) -> ["pr:owner/repo#42", ...]
```

每个 callsite（`start_challenger`、`_verifier.invoke_verifier`、`_verifier.start_fixer`、
`create_staging_test._dispatch_bkd_agent`、`create_pr_ci_watch._dispatch_bkd_agent`、
`create_accept`、`done_archive`）创 issue 前调一次 `ensure_pr_links_in_ctx`，把
返回的 tag 数组拼进 `tags=`。

`start_analyze` / `start_analyze_with_finalized_intent` 不调 helper（创 issue
时 PR 还不存在），但 stash `analyze_issue_id` 进 ctx —— 这样后续 helper 第一次
discover 成功时能回填 analyze issue 的 tag。

### 行为契约

```
ensure_pr_links_in_ctx(req_id, branch, ctx) called from any action
  ↓
1. cached := from_ctx(ctx)  # parses ctx.pr_links list[dict]
2. if cached: return cached
3. repos := discover_repos_via_runner(req_id)  # ls /workspace/source/*/ + git remote
   if no controller / exec error: return []
4. for repo in repos:
     GET /repos/{repo}/pulls?head={owner}:{branch}&state=open
     on httpx error or empty: skip, log warning
     else: links.append(PrLink(repo, pr.number, pr.html_url))
5. if not links: return []
6. update_context(req_id, {pr_links: [l.to_dict() for l in links]})
7. backfill_ids := gather_issue_ids_from_ctx(ctx)  # analyze_issue_id, etc.
   for iid in backfill_ids:
     bkd.merge_tags_and_update(project, iid, add=pr_link_tags(links))
     on error: log warning, continue
8. return links
```

### Tag 形式

`pr:<owner>/<repo>#<number>` —— 跟现有 sisyphus tag 命名约定一致：
`parent-id:`、`verify:`、`trigger:`、`fixer:`、`repo:`、`round:`、`reason:`，
都是 `<key>:<value>` 形式。`#` 在 BKD tag 里没特殊含义（free-form 字符串），
但人眼能立刻识别"是个 PR 编号"。

### 不动什么

- 不改 state.py / engine.py / migrations / router 匹配规则
- 不动现有 webhook / verifier-decision schema
- 不改 BKD intake / analyze prompt（agent 仍按当前 prompt 行事）
- 不引入新 Postgres 表（pr_links 走现有 ctx.jsonb）
- intake issue / 已 archive 的旧 REQ 不回填（不在 active context 里）

## Tradeoffs

- **为什么 tag 不是 description / follow-up message** —— BKD tag 在列表 UI
  里直接显示，且 router 已用 tag 做结构化 metadata（`parent-id:` 等）。description
  是非结构化富文本，follow-up 沉到聊天历史里看不到。
- **为什么 lazy discover 而不是 push-to-ctx 在 PR 开的时候** —— 没人主动告诉
  sisyphus "PR 开了"。analyze-agent 在 prompt 里 push branch + gh pr create，但
  不会回写 ctx（agent 只填 result tag）。Lazy discover 是只 GH REST 1-N 次
  per first-time call 的事，比改 prompt 强约束 agent 写回更稳。
- **为什么 in-memory cache 而非每次都查 GH** —— GH PAT 有 5000/h 配额，每个
  REQ 4-8 个 issue 创建窗口，无 cache 会吃 8 × N(repo) 次/REQ；in-ctx cache
  让第一次成功之后所有后续 callsite O(1)。
- **为什么不区分 closed/merged PR 的 tag** —— `pr:owner/repo#42` 永远指向同一
  个 GH 资源 URL，open / closed / merged 状态在 GH 一查便知，sisyphus tag
  没必要镜像。
- **为什么 best-effort（discover 失败不阻断）** —— issue 创建是热路径（pipeline
  推进每个 stage 都过），GH API 抖动 / runner 没起来 / 0 PR（极早期）任何一种
  都不能让 sisyphus 卡住。tag 缺了下次 callsite 还有机会补上。
- **为什么 backfill 只覆盖 analyze_issue_id（+ 路径上其他已知 issue）** —— 只
  backfill ctx 里能直接读到的 id；不主动扫所有 BKD issue。简单 + 正确：第一次
  helper 命中时已知的就是 analyze issue（intent → analyze）。
- **为什么不自动回填已 archive 的 REQ** —— archive 完成 = REQ 终态，回填没人
  看。本 REQ 只覆盖 active REQ 的"还在跑的"issue。

## Impact

- 新文件 `orchestrator/src/orchestrator/pr_links.py`：
  - `PrLink` dataclass
  - `from_ctx(ctx)` / `pr_link_tags(links)` helpers
  - `discover_pr_links(req_id, branch, repos=None)`（含 `_discover_repos_via_runner`）
  - `ensure_pr_links_in_ctx(req_id, branch, ctx, project_id)`（缓存 + 持久化 + 回填）
- 改 `orchestrator/src/orchestrator/actions/start_analyze.py`：
  stash `analyze_issue_id` 到 ctx（让后续 backfill 能找到）
- 改 `orchestrator/src/orchestrator/actions/_verifier.py`：
  `invoke_verifier` / `start_fixer` 在 create_issue 前调 helper，注入 tag
- 改 `orchestrator/src/orchestrator/actions/create_staging_test.py`：
  `_dispatch_bkd_agent` 调 helper
- 改 `orchestrator/src/orchestrator/actions/create_pr_ci_watch.py`：
  `_dispatch_bkd_agent` 调 helper
- 改 `orchestrator/src/orchestrator/actions/create_accept.py`：
  调 helper（注：accept agent issue 创建时 PR 一定存在，应能命中 cache）
- 改 `orchestrator/src/orchestrator/actions/done_archive.py`：
  调 helper
- 改 `orchestrator/src/orchestrator/actions/start_challenger.py`：
  调 helper
- 新测试 `orchestrator/tests/test_pr_links.py`：
  - LP-S1 cache hit returns cached, no GH call
  - LP-S2 discover via runner + GH API on cache miss + persists ctx
  - LP-S3 discover failure (runner exec error) returns empty list
  - LP-S4 GH API HTTP error per repo skips that repo, returns whatever succeeded
  - LP-S5 first-time discover backfills known issue ids in ctx
  - LP-S6 pr_link_tags renders `pr:owner/repo#N` format
  - LP-S7 from_ctx tolerates malformed entries
- 不改 docs/state-machine.md / migrations / router schema
