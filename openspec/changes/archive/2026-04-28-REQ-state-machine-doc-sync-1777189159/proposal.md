# Proposal: docs(state-machine) — sync with #118 #122 #124

## 背景

`docs/state-machine.md` 的 ReqState 表是新人理解状态机的第一站。三个最近 merge
的 PR 让这张表对外信息有出入：

- **#118** `feat(escalate): open GitHub issue when REQ enters ESCALATED` — 给
  `escalate` action 加了 GH issue side-effect。PR #121 已经在 doc 里加了一行
  "设计被 PR #118 重新走 escalate side-effect 路" 的过渡说明，但写错了函数名
  （`gh_incident.file_incident()` ≠ 实际 `gh_incident.open_incident()`）。
- **#122** `feat(escalate): open one GH incident per involved source repo` —
  把 PR #118 的"单仓 inbox"语义改成"每个 involved source repo 一条"。doc
  目前还停在 #118 的"在 source repo 开 GH issue"单数描述。
- **#124** `fix(prompts): done_archive must not auto-merge or push main` —
  done-archive 不再 `gh pr merge --squash --auto` / 不再 `git push origin main`，
  PR / archive landing 留给人审过再合。但 ReqState 表里 `archiving` 行仍写"合
  PR + 关 issue"，跟新策略冲突。

## 范围

只改 `docs/state-machine.md` 一行（`archiving`）+ 一行（`gh-incident-open`）。

**不改**：

- `state.py` —— `GH_INCIDENT_OPEN` 死枚举继续保留（PR #118 既有取舍）。
- `done_archive.md.j2` —— PR #124 已落地，本 REQ 只做 doc 同步。
- `escalate.py` / `gh_incident.py` —— PR #118 + #122 已落地实现。

## 方案

更新 ReqState 表两行：

1. `archiving`：把"合 PR + 关 issue"改成"每仓 `openspec apply` + 写 archive
   结果，不 auto-merge / 不 push main，final merge 由人在每仓审过再合（#124）"。
2. `gh-incident-open`：
   - 修正函数名 `gh_incident.file_incident()` → `gh_incident.open_incident()`。
   - 把"在 source repo 开 GH issue"改成"对**每个 involved source repo**
     （5 层 fallback：intake_finalized_intent / ctx.involved_repos / `repo:` tag
     / `default_involved_repos` / `settings.gh_incident_repo`）独立 POST 一条
     GH issue，URL 写入 `ctx.gh_incident_urls: dict[str, str]`"。
   - 引用同时 #118 + #122 两个 PR。

## 风险

零风险——文档同步，没有运行时行为变化。spec_lint / dev_cross_check / staging_test
跑过即合。
