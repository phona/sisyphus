# Tasks: REQ-flutter-makefile-cookbook-1777133078

## Stage: contract / spec

- [x] docs/cookbook/ttpos-flutter-makefile.md cookbook 主文档
- [x] openspec/changes/REQ-flutter-makefile-cookbook-1777133078/specs/flutter-makefile-contract/spec.md
- [x] openspec/changes/REQ-flutter-makefile-cookbook-1777133078/specs/flutter-makefile-contract/contract.spec.yaml

## Stage: implementation

- [x] docs/cookbook/ttpos-flutter-makefile.md — 10 节内容完整：
  - §0 TL;DR（3 件事）
  - §1 背景：ttpos-flutter 现状（melos + dart scripts，无 Makefile）
  - §2 repo 布局（加 Makefile 后）
  - §3 ci-* target 实现（ci-env / ci-setup / ci-lint / ci-unit-test / ci-integration-test / ci-build）
  - §4 accept-env 契约：Flutter 源仓参与方式（source repo 不提供 accept-env-up/down）
  - §5 BASE_REV 约定（Flutter 版详解）
  - §6 完整 Makefile 范本
  - §7 melos.yaml 配套（test:unit script）
  - §8 phona/ttpos-ci ci-flutter.yml 修正（关联修法）
  - §9 跟 ttpos-arch-lab cookbook 的关系
  - §10 排查清单

## Stage: PR

- [x] git push feat/REQ-flutter-makefile-cookbook-1777133078
- [x] gh pr create（标题、body 完整）
