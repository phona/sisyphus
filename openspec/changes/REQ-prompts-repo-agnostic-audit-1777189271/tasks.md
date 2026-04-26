# Tasks: REQ-prompts-repo-agnostic-audit-1777189271

## Stage: spec

- [x] `openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271/proposal.md`
- [x] `openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271/tasks.md`
- [x] `openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271/specs/prompts-repo-agnostic/spec.md`
- [x] `openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271/specs/prompts-repo-agnostic/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/prompts/analyze.md.j2`：3 处 `phona/repo-a` → `<owner>/repo-a`，
  2 处 "ttpos-ci 标准" → "Makefile ci 契约（详见 docs/integration-contracts.md）"
- [x] `orchestrator/src/orchestrator/prompts/done_archive.md.j2`：5 处 `phona/repo-a` /
  `phona/repo-b` / `phona/xxx` → `<owner>/repo-a` / `<owner>/repo-b` / `<owner>/repo`
- [x] `orchestrator/src/orchestrator/prompts/_shared/runner_container.md.j2`：1 处
  `phona/repo-a phona/repo-b` → `<owner>/repo-a <owner>/repo-b`，
  1 处 "ttpos-ci 标准 target" → "Makefile ci 契约 target"
- [x] `orchestrator/src/orchestrator/prompts/bugfix.md.j2`：line 62
  `/workspace/source/sisyphus` → `/workspace/source/REPO`，
  line 80 "ttpos-ci 契约" → "Makefile ci 契约"
- [x] `orchestrator/src/orchestrator/prompts/staging_test.md.j2`：line 23 "ttpos-ci 标准"
  → "Makefile ci 契约"
- [x] `orchestrator/src/orchestrator/prompts/challenger.md.j2`：line 36 / 150
  `<spec_home_repo>` 在路径里改成 `<spec_home_repo_basename>`（注释说明 basename
  约定），line 136 `gh repo clone phona/<spec_home_repo>` → `gh repo clone <spec_home_repo>`
- [x] `orchestrator/src/orchestrator/prompts/accept.md.j2`：去掉 `FEATURE-A*` 前缀
  3 处（line 28 / 58 / 79-80），改成"spec 里定义的 `#### Scenario:` block"，
  报告 example 用 `<scenario-id>:1` 占位
- [x] `orchestrator/src/orchestrator/prompts/verifier/accept_fail.md.j2`：line 4
  去 `FEATURE-A*`
- [x] `orchestrator/src/orchestrator/prompts/verifier/accept_success.md.j2`：line 4 / 10
  去 `FEATURE-A*`
- [x] `orchestrator/src/orchestrator/prompts/verifier/dev_cross_check_fail.md.j2`：
  line 6 "ttpos-ci 标准" → "Makefile ci 契约"，line 18 example
  `phona/repo-a` → `<owner>/repo-a`
- [x] `orchestrator/src/orchestrator/prompts/verifier/dev_cross_check_success.md.j2`：
  line 6 "ttpos-ci 标准" → "Makefile ci 契约"
- [x] `orchestrator/src/orchestrator/prompts/verifier/spec_lint_fail.md.j2`：
  line 20 example `phona/repo-a` → `<owner>/repo-a`
- [x] `orchestrator/src/orchestrator/prompts/verifier/_decision.md.j2`：line 22
  `"target_repo": "phona/repo-a"` → `"target_repo": "<owner>/repo-a"`

## Stage: validate

- [x] `openspec validate openspec/changes/REQ-prompts-repo-agnostic-audit-1777189271`
  通过
- [x] grep 不变量复测：`grep -RE 'phona/(repo-a|repo-b|xxx|<spec_home_repo>)|ttpos-ci 标准|FEATURE-A\*|/workspace/source/sisyphus' orchestrator/src/orchestrator/prompts/`
  零匹配

## Stage: PR

- [x] git push feat/REQ-prompts-repo-agnostic-audit-1777189271
- [x] gh pr create
