# Tasks: REQ-audit-business-repo-makefile-1777125538

只读审计 REQ。所有任务都是审计执行 + 沉淀，**不改业务 repo**。

## Stage: contract / spec

- [x] 读 phona/ttpos-ci/README.md "业务仓库 CI 契约" 段确定契约真值
- [x] 读 phona/ttpos-ci/.github/workflows/{ci-go.yml, ci-flutter.yml} 看实际调用差异
- [x] 读 phona/sisyphus/docs/integration-contracts.md §2.1 + §2.2 看 sisyphus 强约束子集
- [x] 把审计核对项写成 `specs/business-repo-onboarding-audit/spec.md` 复用 spec
      （ADDED Requirements；onboarding 时新仓必须过这一遍）

## Stage: audit execution

- [x] 通过 `gh api` 拉 ZonEaseTech/ttpos-server-go 的 Makefile + ttpos-scripts/lint-ci-test.mk
- [x] 通过 `gh api` 拉 ZonEaseTech/ttpos-flutter 的根目录清单（release branch）+ scripts/ + .github/workflows
- [x] 验证两仓的 default branch（确认不是 main）
- [x] 查 ttpos-server-go 是否有 dispatch.yml（结果：有）
- [x] 查 ttpos-flutter 是否有 dispatch.yml（结果：无）
- [x] 找 phona/ttpos-server-go vs ZonEaseTech/ttpos-server-go 命名错位

## Stage: report

- [x] 写 `audit-report.md`，逐 target × 逐仓打 PASS / GAP / WARN / STUB
- [x] 写 `proposal.md` 摘要 + 取舍 + 后续 REQ 候选
- [x] 标出契约文档自身不一致（BASE_REV vs BASE_REF；phona/ vs ZonEaseTech/；非主流默认分支 BASE_REV 行为未文档化）

## Stage: PR

- [x] git push feat/REQ-audit-business-repo-makefile-1777125538
- [x] gh pr create（标题 + body 写清楚是只读审计 REQ）

## 不做的事（明确边界）

- [ ] ~~改 ttpos-server-go 的 ci-build stub~~ —— 业务仓 owner 决策；本 REQ 范围外
- [ ] ~~给 ttpos-flutter 加 Makefile~~ —— 路径 A vs 路径 B 没拍板，需要先有设计 REQ
- [ ] ~~修 phona/ttpos-ci 的 ci-flutter.yml BASE_REF 拼写~~ —— 独立 doc/CI fix REQ
- [ ] ~~改 docs/integration-contracts.md 里 phona/ttpos-* 错位~~ —— 独立 doc fix REQ
- [ ] ~~把 ttpos-server-go 默认分支改 main~~ —— 业务仓 owner 决策

这些 GAP / 不一致已在 audit-report.md §6 列成后续 REQ 候选，等业务方决策再独立起。
