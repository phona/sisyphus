# Proposal: ttpos-flutter 根 Makefile wrapper cookbook

## 背景

REQ-audit-business-repo-makefile-1777125538 审计发现 `ZonEaseTech/ttpos-flutter`
repo 根目录没有 Makefile。该仓使用 melos + dart scripts + bun 构建，完全不走 Make。

结果：sisyphus 三大机械 checker（dev_cross_check `ci-lint`、staging-test `ci-unit-test`
/ `ci-integration-test`）在 runner pod 里直接跑 `make ci-*`，全部报 `No rule to make target`，
立即红。ttpos-flutter 当前**无法接入 sisyphus involved_repos**。

审计给出修复路径 A（本 REQ 落地的方向）：在 Flutter 仓根目录加一份薄 Makefile，
把 melos / dart 命令包成 ttpos-ci 标准 target。

## 范围

本 REQ 的产物是 **sisyphus 仓内的一份 cookbook 文档**
（`docs/cookbook/ttpos-flutter-makefile.md`）。它指导 ttpos-flutter 仓（或任何
melos Flutter 多包工作区）的工程师如何写根 Makefile，以满足 sisyphus 接入要求。

**不改** ttpos-flutter / ttpos-arch-lab / ttpos-ci 业务仓本身 —— 那是后续独立 REQ
（接入实现 REQ）的工作，由业务团队按本 cookbook 落地。

## 方案

Makefile wrapper 模式（路径 A）：

```
根 Makefile（新增）
  └── ci-env          → 输出 FLUTTER_VERSION 等 key=value
  └── ci-setup        → flutter pub get + melos bootstrap
  └── ci-lint         → flutter analyze --no-pub + dart format --set-exit-if-changed
  └── ci-unit-test    → melos run test:unit（无 device）
  └── ci-integration-test → 空实现 exit 0（或 docker compose 后端集成）
  └── ci-build        → flutter build apk --release
```

Makefile 不重复内部构建逻辑，只做 target 委托，侵入最小。

## 关键设计决策

1. **BASE_REV 全量扫**：`flutter analyze` 无 `--new-from-rev` 等价；接受 BASE_REV
   env 但始终全量扫。文档化为已知行为。

2. **ci-integration-test 默认空实现**：Flutter integration test 需要 emulator；
   staging-test 阶段 runner pod 没有 emulator（emulator 是 accept 阶段 arch-lab
   的工作）。默认 exit 0，进阶提供 docker compose 后端集成选项。

3. **accept-env 不由 Flutter 源仓提供**：Flutter 源仓是 source repo；
   accept-env-up/down 由 arch-lab integration repo 提供。
   arch-lab 从 `/workspace/source/ttpos-flutter/` 读源码编 APK。

4. **melos.yaml 配套**：cookbook 包含 `scripts.test:unit` 的示例定义，
   确保 `melos run test:unit` 有效。

## 依赖 / 前置

- docs/cookbook/ 目录已存在（ttpos-arch-lab-accept-env.md 已在里面）
- openspec 已在 sisyphus 仓初始化
- 无跨仓依赖（只改 sisyphus 仓文档）
