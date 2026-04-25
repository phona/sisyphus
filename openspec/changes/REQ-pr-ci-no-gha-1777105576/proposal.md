# REQ-pr-ci-no-gha-1777105576: fix(pr_ci_watch): treat all-green-but-no-GHA check-runs as fail

## 问题

`pr_ci_watch._classify` 把"全 completed + 全绿"无条件判 `pass`，**不检查这些
check-run 是否真的来自 GitHub Actions**。当 PR 目标分支不在源仓 `ci.yml` 触发
列表（force-push 落到非 `main` 分支 / workflow 被禁 / GHA webhook miss 整套）时，
GHA 一次没跑；GitHub 上只剩 `claude-review` 这种 review-only bot 报 success，
旧实现把它当全绿 pass —— sisyphus 直接推进到 accept 阶段，原 bug 还在。

真实事故（REQ-acceptance-e2e-1777084500，2026-04-25 03:32）：dev-fixer push 后
PR #205 上只有 `app.slug == "anthropic-claude"` 的 `claude-review` check-run 报
success；pr_ci_watch 返 PR_CI_PASS；verifier-agent 二次审才 catch 到"GHA 一次
没跑"，主观判 escalate。**机械层应该自己识别这种假阳性 pass，不让 verifier 兜底**。

## 方案

`pr_ci_watch._classify` 增加 `no-gha` verdict：全绿但 0 条 GHA check-run → 不当 pass。

GHA 识别规则：check-run 的 `app.slug == "github-actions"`。GitHub REST 文档约定
`/repos/{repo}/commits/{sha}/check-runs` 返回的每条 run 含 `app.slug` 标识哪个
GitHub App 跑的；GHA workflow 产的 check-run 这个字段恒为 `"github-actions"`。

`watch_pr_ci` 主循环把 `no-gha` 与 `fail` 同等对待 → 返 `passed=False, exit_code=1`，
`stdout_tail` 形如 `<repo>: no-gha-checks-ran (only non-CI signals: <name>=<conclusion> ...)`，
让 verifier / 人工审一眼能看出"GHA 没跑"。

边界：
- 有任一 pending check-run → 仍判 `pending`（GHA workflow 可能刚要起，给它机会）
- 混合 GHA + review-only 且全绿 → `pass`（review bot 不污染正常 CI 通过判定）
- check-run 缺 `app` 字段 → 保守当非 GHA（未知来源不能撑 pass）
- 空 runs（PR 刚开 0 条 check-run）→ 仍判 `pending`（与原行为一致，给 GHA 上车机会，
  最终超时走 124）

## 范围

只改 `orchestrator/src/orchestrator/checkers/pr_ci_watch.py` 及其测试。
不改状态机 / 事件 / DB schema / verifier prompt。pr_ci verifier 收到
`PR_CI_FAIL` reason `no-gha-checks-ran` 走原来的失败决策路径即可。

## 不做

- 不在 prod 把 `_GHA_APP_SLUG` 设成可配置 —— 整个 sisyphus 流水线契约就锁死
  GHA，没必要把"什么算 CI"做成开关。
- 不改 verifier prompt —— 现有 `verifier/pr_ci_fail.md.j2` 已经能基于 stdout_tail
  做决策；新增 `no-gha-checks-ran` reason 是字符串自描述，verifier 读得懂。
- 不动 SHA flip / merge / closed 已有 terminal 路径 —— 它们和 `_classify` 是
  正交两套判决，互不影响。
