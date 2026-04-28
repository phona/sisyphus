# REQ-bkd-cleanup-historical-review-1777222384: chore(bkd): backfill review-stuck sub-issues to done

## Why

BKD 看板的 "review" 列被历史 sub-issue 堆满。截至 2026-04-26，project `nnvxh8wj`
有 46 条 `statusId='review'` 的 issue（用户的初步估算是 ~55，量级一致），全部是
sub-agent 类型的 issue（不是 user 创的 intent issue）：

- 40 条 `verifier` + `sessionStatus='completed'` —— webhook 在 VERIFY_ESCALATE 路径
  上**故意**把 BKD status 推到 "review"（让用户能看到待 follow-up 的入口），但当
  REQ 后续被 admin 推 done / pr-merged-override 短路终态后，原 verifier issue 的
  BKD status 没有被反向收尾，永久留在 "review"
- 3 条 `fixer` + `sessionStatus='failed'` / 2 条 `analyze` + `sessionStatus='failed'` /
  1 条 `challenger` + `sessionStatus='failed'` —— webhook 的 `_push_upstream_status`
  只在 `body.event == 'session.completed'` 才推（webhook.py:233），`session.failed`
  不推；BKD agent 失败后 statusId 维持原值（多半是预先被 follow-up 之前的
  `working` 一路保留，再被某个旧路径写成 review）

这些 issue 全部 `sessionStatus in {completed, failed}`（无 `running`），其母 REQ 要么
已经 DONE / ESCALATED 终态，要么早已被新一轮人工 / sub-agent 重新接管 —— 它们对
当前活流没有任何作用，但污染：

1. **BKD 看板 UI** —— "review" 列堆 40+ 历史 issue，用户找当前要 follow-up 的入口
   要翻很久
2. **观测面** —— `verifier_decisions` / `stage_runs` 表已记完工号志，BKD 那边的
   "review" 状态对仪表盘没价值，反而误导 "看板上还有 N 个待审"
3. **dedup / observability** —— 后台清理脚本扫 review 列时被噪音淹没

需要一次性 backfill：把这 46 条（实际跑时取 live 列表，量级 40-55）安全地推到
`statusId='done'`。

## What Changes

加一个 **一次性 maintenance CLI**：`orchestrator/src/orchestrator/maintenance/backfill_bkd_review_stuck.py`

跑法：

```bash
cd orchestrator
uv run python -m orchestrator.maintenance.backfill_bkd_review_stuck \
  --project nnvxh8wj --bkd-base-url http://localhost:3000 [--apply]
```

不加 `--apply` 即 dry-run，stdout 列出每条候选 + decision_reason，不动任何状态。
加 `--apply` 才真发 PATCH。

### 决策契约

对项目里每条 BKD issue 应用以下 filter（顺序短路）：

```
def is_safe_target(issue) -> tuple[bool, str]:
    if issue.statusId != "review":     return (False, "not-review")
    if issue.sessionStatus == "running":return (False, "session-running")
    role = first_role_tag(issue.tags)  # verifier / fixer / analyze / challenger / accept-agent / done-archive
    if role is None:                   return (False, "no-role-tag")  # 保护 user 创的 intent issue
    if not has_req_tag(issue.tags):    return (False, "no-req-tag")   # 防误伤无 REQ 关联的孤儿
    return (True, f"role={role};session={issue.sessionStatus}")
```

通过 filter 的逐条 PATCH `statusId='done'`，tags 不动（保留 REQ-* / 阶段 tag /
verifier decision tag —— 让审计能回放 "这条 issue 历史上跑了什么"）。

### 为什么不用 admin REST endpoint / 不入热路径

- **不是状态机职责** —— sisyphus 的 `req_state` 已经是 source of truth，BKD UI 状态
  只是显示用。这次 cleanup 不动 sisyphus 状态，只 PATCH BKD UI；admin endpoint
  路径会让 caller 误以为这是 REQ 状态变更
- **一次性，不需要常驻** —— 加 admin endpoint = 加路由 + 加 token 鉴权 + 加常驻代码
  路径，跑一次扔了不划算。后续再有同类需求再写一次脚本，diff 比维护一坨"通用清理"
  小
- **不修 webhook hot-path** —— webhook.py:233 只推 completed 不推 failed 是 **有意
  设计**：session.failed 在 sub-agent 类（fixer/analyze/challenger）上可能值得人
  审视。改它要重新评估 router 行为，超出本 REQ 范围。本 REQ 只清历史 stuck

## Tradeoffs

- **为什么 dry-run 默认而不是 apply 默认** —— 不可逆操作（PATCH BKD status）。
  默认 dry-run 让 caller 先看清楚再点。mirror `kubectl apply` 之于 `--dry-run=client`
- **为什么不 backfill 直接走 sisyphus 内 BKDClient** —— BKDClient 配置依赖 settings
  + DI（pool / observability hooks 等），跑一次性脚本不值得拉起完整 app context。
  脚本 import `bkd_rest.BKDRestClient` 直接走 httpx，最小依赖
- **为什么不写额外的 sisyphus state 反查** —— 候选 filter 已经够保守：role-tagged
  + session 非 running。即便母 REQ 还活着，也不会有 sub-issue 在 sessionStatus !=
  running 时被 sisyphus 重新路由（sisyphus 不依赖 BKD UI status，依赖 webhook 事件
  + req_state row）。把 BKD UI status 推 "done" 不影响任何 sisyphus 决策
- **为什么不删 BKD issue** —— 删了观测面取数源没了（verifier_decisions / stage_runs
  跨表 join 还要回查 BKD 元信息）。"done" 是 BKD UI 里的 "归档" 列，正合适
- **为什么不区分 verifier-completed-escalate vs verifier-completed-pass** —— 都已经
  REQ 终态。verifier 的 follow-up 入口在它的 BKD issue 本身（user click "follow up"
  按钮即可），跟 statusId 无关。维持 "review" 没有功能意义

## Impact

- 新增 `orchestrator/src/orchestrator/maintenance/__init__.py`（空）
- 新增 `orchestrator/src/orchestrator/maintenance/backfill_bkd_review_stuck.py`
  - `is_safe_target(issue) -> tuple[bool, str]` 纯函数（pytest 可白盒）
  - `select_targets(issues: list[Issue]) -> list[Issue]` filter pipeline
  - `async def run(project_id: str, bkd_base_url: str, apply: bool, ...)` 主流
  - `main()` argparse + `asyncio.run`
- 新增 `orchestrator/tests/test_backfill_bkd_review_stuck.py`
  - BBR-S1..S6 unit scenarios（详见 spec.md）
- 不动 `webhook.py` / `engine.py` / `actions/*.py` / `state.py` / migrations
- 不动 BKD tag schema / state-machine.md / docs/architecture.md
- 跑一次后产出：cleaned issue count + audit log（stdout JSON）—— 落 PR description
  作为 evidence
