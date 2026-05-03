# Tasks — REQ-impl-thanatos-scenarios-fallback-1777807813

## Stage: spec

- [x] 写 proposal.md
- [x] 写 specs/thanatos/spec.md（ADDED Requirement: R9 fallback resolver）
- [x] 写本 tasks.md

## Stage: implementation

- [x] `thanatos/src/thanatos/skill.py` 新增 `resolve_skill_path(repo_root, *, filename="skill.yaml")`
  - 先查 `<repo_root>/.sisyphus/scenarios/` 目录存在 + 非空
  - 否则 fallback `<repo_root>/.thanatos/` 目录存在
  - 都无 → `SkillLoadError`
- [x] `thanatos/tests/test_skill_loader.py` 增加 6 条单测：
  - CREO-S32 priority（双都在 → sisyphus 胜）
  - CREO-S33 sisyphus 缺 → fallback thanatos
  - CREO-S34 sisyphus 空目录 → fallback thanatos
  - CREO-S35 都缺 → SkillLoadError
  - resolve + load_skill end-to-end pipeline
  - custom filename 参数透传

## Stage: PR（推之前必须全绿）

- [x] 本仓 `make ci-lint` 全绿（仅 lint 改动文件）
- [x] 本仓 `make ci-unit-test` 全绿（thanatos 套件 + orchestrator 套件）
- [x] 本仓 `make ci-integration-test` 全绿或自动 skip（无 PG 环境视为 pass）
- [x] git push feat/REQ-impl-thanatos-scenarios-fallback-1777807813
- [x] gh pr create --label sisyphus
