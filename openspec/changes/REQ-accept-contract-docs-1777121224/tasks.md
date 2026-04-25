# Tasks: REQ-accept-contract-docs-1777121224

## Stage: spec

- [x] 写 `proposal.md` 详述根因 + 方案 + 取舍
- [x] 写 `specs/accept-env-target-naming/spec.md`（ADDED Requirements + Scenario，覆盖每个被改的文档面）
- [x] tasks.md（本文件，每个 checkbox 反映真实交付状态）

## Stage: docs

- [x] `docs/integration-contracts.md` §1 介绍：accept-up/down → ci-accept-env-up/down
- [x] `docs/integration-contracts.md` §2.3 整数 source/integration repo target 表：target 列改名 + 调用路径里的 `make accept-up` / `accept-down` 改名
- [x] `docs/integration-contracts.md` §3 章节标题与内文：`accept-up 的 stdout JSON 契约` → `ci-accept-env-up 的 stdout JSON 契约`
- [x] `docs/integration-contracts.md` §3 实现建议代码块：target 名 `accept-up:` → `ci-accept-env-up:`
- [x] `docs/integration-contracts.md` §4.2 helm 模板：target 名 + `.PHONY` 改名
- [x] `docs/integration-contracts.md` §4.2.2 **新增 docker-compose 模板** （`ci-accept-env-up` 起 stack，`ci-accept-env-down` 拆 + 卷清理）
- [x] `docs/integration-contracts.md` §5 env 表 `SISYPHUS_STAGE` 行：`accept-up / accept-down` 改名
- [x] `docs/integration-contracts.md` §8 排查清单第 4 条：`accept-up 失败` 改名

- [x] `docs/architecture.md` §2 mermaid `EnvUp` / `Teardown` 节点 label：`make accept-up` / `make accept-down` 改名
- [x] `docs/architecture.md` §5 角色分工表 "机械 checker" 行：`accept-up/down` → `ci-accept-env-up/down`
- [x] `docs/architecture.md` §6 Stage 表 7a / 8 行：`make accept-up` / `make accept-down` 改名
- [x] `docs/architecture.md` §7 数据流原语表 `make accept-up / accept-down` 改名
- [x] `docs/architecture.md` §8 env 表 `SISYPHUS_STAGE` 行：`accept-up / accept-down` 改名
- [x] `docs/architecture.md` §13 演进路线：`accept-up / accept-down 落到生产 lab` 改名

- [x] `CLAUDE.md` "Stage 流" 一行：`make accept-up` / `make accept-down 必跑` 改名

- [x] `README.md` §"当前架构" mermaid `EnvUp` / `Teardown` 节点 label：`make accept-up` / `make accept-down` 改名
- [x] `README.md` §"接入新业务 repo" 表两行 `make accept-up` / `make accept-down` 改名

## Stage: verify

- [x] `grep -RIn 'make accept-up\|make accept-down' docs/ README.md CLAUDE.md` 零命中
- [x] `grep -RIn '^accept-up:\|^accept-down:' docs/` 零命中（确认 Makefile target 头都改了）
- [x] `grep -RIn 'ci-accept-env-up\|ci-accept-env-down' docs/integration-contracts.md` ≥ 6 处命中（§2.3 / §3 / §4.2 / §4.2.2 / §5 / §8）
- [x] `openspec validate openspec/changes/REQ-accept-contract-docs-1777121224 --strict` 通过
- [x] `make ci-lint && make ci-unit-test && make ci-integration-test` 全过（零行为变化只须不打破 self-dogfood）

## Stage: PR

- [x] commit feat/REQ-accept-contract-docs-1777121224 + push origin
- [x] gh pr create（标题 `docs(contracts): rename accept-up/down → ci-accept-env-up/down + docker-compose template`）
