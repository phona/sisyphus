# Tasks: REQ-flutter-mobile-accept-cookbook-1777247423

## Stage: contract / spec

- [x] openspec/changes/REQ-flutter-mobile-accept-cookbook-1777247423/proposal.md
- [x] openspec/changes/REQ-flutter-mobile-accept-cookbook-1777247423/specs/mobile-accept-cookbook/spec.md
- [x] openspec/changes/REQ-flutter-mobile-accept-cookbook-1777247423/specs/mobile-accept-cookbook/contract.spec.yaml

## Stage: implementation

- [x] docs/cookbook/ttpos-flutter-mobile-accept-env.md — 9 节内容完整：
  - §0 TL;DR（3 件事）
  - §1 决策树：什么时候选本 cookbook（vs arch-lab）
  - §2 repo 布局（Flutter 仓自承 integration repo 后）
  - §3 mock backend stack（tests/docker-compose.accept.yml 范本）
  - §4 Makefile: accept-env-up / accept-env-down 完整 recipe
  - §5 accept-agent 怎么用 endpoint（scenario 限制表）
  - §6 SISYPHUS_NAMESPACE 与并发隔离
  - §7 跟其它 cookbook 的关系（三方对比表 + 流程图）
  - §8 排查清单
  - §9 不要做的事（反模式）
- [x] docs/cookbook/ttpos-flutter-makefile.md §4.3 — 留 minimal Makefile 片段做
  索引示例，把"完整食谱"指引到新 cookbook（避免重复维护两份）
- [x] docs/cookbook/ttpos-flutter-makefile.md §9 —— 关系表从 2 列扩展到 3 列
  （加 mobile-accept-env cookbook）
- [x] docs/integration-contracts.md §4.2.2 —— 在 mobile e2e 引用块下追加
  Flutter self-hosted 路径引用，让查契约的人能找到该入口

## Stage: PR

- [x] git push feat/REQ-flutter-mobile-accept-cookbook-1777247423
- [x] gh pr create（标题、body 完整）+ `--label sisyphus`
