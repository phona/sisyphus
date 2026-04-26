# REQ-archive-state-cleanup-1777195098: fix(state): mark merged-PR REQs done not stuck escalated

## Why

REQ 的 PR 已经被合并，但因 pipeline 下游某 stage 红了（pr-ci timeout / accept lab
起不来 / archive agent 崩 / verifier 判 escalate），sisyphus 把 REQ 推进 ESCALATED。

人在 dashboard 上看到的画面：

- BKD 上 PR 标 `merged`
- sisyphus `req_state` 行 `state='escalated'`
- 多打一条 `escalated` + `reason:<...>` tag
- 多开一条 GH incident issue（`escalate.py` per-involved-repo loop 的副作用）

人需要手动确认"哦 PR 已经 merge 了，REQ 可以收"，然后调
`POST /admin/req/{req_id}/complete` 才能清掉。如果不清，PVC 在
`pvc_retain_on_escalate_days` 期内一直占磁盘，dashboard 的 `failure_mode` view 也
把这些"假阴性 escalated"计入 reason 频次（污染 Top-N）。

典型触发场景：

1. **pr-ci-watch 看到 merged PR 之外的 stage 红** —— 比如 PR 在 GHA 跑完之前就被
   manual merge 了，pr-ci-watch 把 merged 当 pass，**但 accept lab 起不来** →
   `accept-env-up.fail` → ESCALATED。PR 已 merged，accept lab 故障是 infra 问题，
   不该占用 escalated 队列。
2. **archive agent crash on openspec apply** —— PR 已 merged（dev 流程末尾人工
   merge），done-archive agent 在跑 `openspec apply` 时崩了 → SESSION_FAILED →
   retry 用完 → ESCALATED。但 REQ 实际已交付。
3. **verifier-decision-escalate after 部分修复** —— verifier 在 review stage 判
   escalate，但人在 BKD 看了之后直接 merge 了 PR（"我看 stage 红的是 lint，但
   实质功能 OK，我接管"）。sisyphus 不会自动反应。

这三类有共性：**PR 都在 GitHub 上已 merge，REQ 实际状态是 done，sisyphus 当 escalated 是误诊**。

## What Changes

在 `actions/escalate.py` 的入口加一道 GitHub REST 探测：当本 REQ 的 involved_repos
（layer 1-4 fallback：`intake_finalized_intent.involved_repos` /
`ctx.involved_repos` / `repo:<slug>` tag / `settings.default_involved_repos`）所有
**实际开过 PR** 的仓的 `feat/{REQ}` PR **全部 merged**，escalate 直接把 REQ CAS 到
DONE，跳过：

- 真 escalate 路径（不写 escalated tag、不写 reason 细分 tag）
- GH incident issue 创建（这条 REQ 不算 incident）
- runner cleanup 用 `retain_pvc=False`（mirror admin/complete：done 立即收磁盘）

不动 state.py（不加新 transition / event）—— 这是 escalate action 在被调用的瞬间
"discover terminal state via side channel"，跟 admin/complete 同模式。

### 行为契约

```
escalate(body, req_id, tags, ctx) called from engine.step
  ↓
1. resolve involved_repos via _clone.resolve_repos (layers 1-4, NOT gh_incident_repo layer 5)
2. if repos non-empty AND github_token set:
     query GH `/repos/{owner}/{repo}/pulls?head={owner}:feat/{req}&state=all`
     if ≥1 repo has a PR AND every found PR has merged_at != null → all_merged=True
   else all_merged=False
3. if all_merged:
     CAS state → DONE (raw cas_transition with Event.ARCHIVE_DONE
                       and action="escalate_pr_merged_override")
     update_context: {completed_via:"pr-merge", completed_from_state:<prev>,
                      completed_repos:[...]}
     PATCH BKD intent issue: tags add ["done", "via:pr-merge"], statusId="done"
     fire-and-forget cleanup_runner(retain_pvc=False)
     return {"escalated": False, "completed_via": "pr-merge", ...}
4. else:
     proceed with existing escalate flow (auto-resume / GH incident / ESCALATED tag)
```

### 检查的 PR 含义

- "存在的 PR" = GH `/pulls?head={owner}:feat/{req_id}&state=all` 至少返回一行
- "merged" = 该行 `merged_at != null`
- 如果 involved_repos 里**有的仓没开过 PR**（dev 没改这个仓 / 还没 push），不计入分母
- 如果**所有 involved_repos 都没开过 PR**（早 stage 失败、还没 dev push），all_merged=False
- 至少 1 个 PR 找到 + 全 merged → all_merged=True

### 与现有路径的对比

| 路径 | 触发 | from_state | to_state | retain PVC | 谁判 |
|---|---|---|---|---|---|
| 主链 archive_done | ARCHIVING + archive.done | archiving | done | no | sisyphus |
| **本 REQ 的 pr-merged-override** | **任意 escalate 触发 + GH PR 全 merged** | **任意（含 ESCALATED）** | **done** | **no** | **sisyphus** |
| admin/complete | admin 手调 | escalated | done | no | human |
| escalate（原路径） | escalate event 且 PR 未全 merged | * → escalated | escalated | yes | sisyphus |

本 REQ 的 override 跟 admin/complete 同终点（DONE + cleanup retain_pvc=False），但
**自动**触发，不依赖 human 巡检。同时不替代 admin/complete —— "PR 没 merge 但 stale
不会续"的场景仍然要 admin 手调。

## Tradeoffs

- **为什么放 escalate.py 入口而非 webhook router** —— router 拿不到 GH 状态，做
  REST 探测属于 action 层职责。escalate 是统一入口（所有进 ESCALATED 的路径都过它），
  在这放一道前置 check 覆盖最广，跟 escalate 已有的 transient/auto-resume/incident
  分支并列。
- **为什么不加 periodic sweep 扫 ESCALATED 表** —— 多一个后台 loop 多一类故障面。
  "PR 在 escalate 之后才被 merge"的窗口已有 admin/complete 兜底（本来就是它的设计
  用意：human 决定不会续 → 收尾）。escalate-time check 命中"escalate 之前 / 同时
  PR 已 merge"的主流场景就够了。
- **为什么不读 layer 5 `gh_incident_repo`** —— 那是 intake-stage 失败 pre-clone 的
  legacy single-inbox（事故 issue 集中收）。不是该 REQ 实际触发的源仓。在那里查
  `feat/REQ` 注定查不到（intake 没 push 任何分支），徒增一次 404。
- **为什么不缓存 GH 探测结果 / 加 metrics** —— 触发频率受限于 escalate 频率（一个
  REQ 顶多 escalate 一次再加 watchdog 重试 N 次），N≤3 个 GH REST call per escalate
  对配额完全无压力（GH PAT 5000/h）。metrics 由现有的 `router.decision`
  observability 事件捕获（escalate 已有），看 ctx.completed_via 字段就能区分。
- **为什么 to_state 用 ARCHIVE_DONE 事件 label 而非新事件** —— ARCHIVE_DONE 语义
  "REQ 归档完成"对"PR 已 merged 视作归档"完美对齐。不加新 Event 枚举值 = 不动
  state.py = transition 表保持闭。history.action 用 `escalate_pr_merged_override`
  字符串区分这是 override 路径而非主链 ARCHIVING + ARCHIVE_DONE。
- **为什么不需要新 BKD tag schema** —— `done` + `via:pr-merge` 两个 tag 描述清楚。
  `via:pr-merge` 是新 tag，但 BKD tag 是 free-form，没 schema 约束（router 不
  match `via:*` 前缀）。方便 dashboard SQL `WHERE tags @> 'via:pr-merge'` 查统计。

## Impact

- 改 `orchestrator/src/orchestrator/actions/escalate.py`：
  - 加 `_all_prs_merged_for_req(repos: list[str], branch: str) -> bool` helper
  - 加 `_apply_pr_merged_done_override(...)` helper（CAS + ctx + BKD + cleanup）
  - 在 `escalate()` 入口（auto-resume 之前）调上面两个 helper；命中即 return early
- 改 `docs/state-machine.md`：在 §8 "session.failed 兜底" 后新增一节描述 escalate
  入口的 PR-merged shortcut
- 测试 `orchestrator/tests/test_contract_escalate_pr_merged_override.py`（新文件）：
  - PMO-S1 single repo, PR merged → state=done, no escalated tag
  - PMO-S2 single repo, PR open → 走原 escalate 路径
  - PMO-S3 multi repo, all merged → state=done
  - PMO-S4 multi repo, partial merged (one open) → 走原 escalate 路径
  - PMO-S5 no involved_repos → 走原 escalate 路径
  - PMO-S6 GH API error → 走原 escalate 路径（不阻塞）
  - PMO-S7 cleanup_runner called with retain_pvc=False on override path
  - PMO-S8 BKD tag includes "done" + "via:pr-merge", NOT "escalated"
- 不动 `state.py`、`engine.py`、postgres migrations、watchdog.py、admin.py
- 不动 BKD tag schema docs / router 匹配规则
