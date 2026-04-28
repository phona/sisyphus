# REQ-audit-business-repo-makefile-1777125538: audit(read-only) — ttpos-server-go + ttpos-flutter Makefile coverage of ttpos-ci contract

## Why

sisyphus 没有任何预飞行合规 check —— 一个新 source repo 进 `involved_repos` 后，
dev_cross_check 第一次跑才会发现 target 缺，错误形态是
`make: *** No rule to make target 'ci-lint'`，verifier-agent 得人工识别成
"基础设施缺失"而非业务 bug。REQ-default-involved-repos-1777124541 起 sisyphus
默认带 `[phona/sisyphus]` 自托管单仓跑通了；下一步要把 ttpos 业务双仓
（ttpos-server-go / ttpos-flutter）加进 involved_repos 之前，先把现状摸一遍：
哪些 repo 已经合规、哪些缺、缺哪几条。本 REQ 是只读审计，**不改业务 repo 任何
文件**，也不改 sisyphus 行为代码；交付物只有 `audit-report.md` + 一份可复用
onboarding checklist spec。修复缺口由独立 follow-up REQ 承接。

## What Changes

- 新增 capability `business-repo-onboarding-audit` 沉淀本次审计的核对项（5 条
  ADDED Requirements），未来再加 source repo 时直接拿这份 checklist 跑一遍
- 新增审计交付文档 `openspec/changes/REQ-audit-business-repo-makefile-1777125538/audit-report.md`
- 不改业务 repo（ZonEaseTech/ttpos-server-go / ZonEaseTech/ttpos-flutter）任何文件
- 不改 sisyphus orchestrator 行为代码
- 不修任何 GAP（修复留给独立 follow-up REQ；候选清单见 audit-report §6）

## 任务性质

**只读审计**。本 REQ 不改业务 repo（ttpos-server-go / ttpos-flutter）任何文件，
也不改 sisyphus 行为代码。交付物只有两件：

1. `audit-report.md` —— 把 ttpos-ci 契约 vs 两个业务 repo 的 Makefile 现状逐条对齐，
   列出 **PASS / GAP / DOCS-MISMATCH**
2. `specs/business-repo-onboarding-audit/spec.md` —— 把这次审计沉淀成一份**可复用
   onboarding checklist**，未来再加 source repo 时直接拿这份去过一遍

修复缺口（GAP）由独立 follow-up REQ 承接（本 REQ 不背负实现职责）。

## 为什么要做这次审计

`docs/integration-contracts.md` 的硬约束是：source repo 必须实现 `ci-lint` /
`ci-unit-test` / `ci-integration-test` 三个 ttpos-ci 标准 target，sisyphus
的机械 checker（dev_cross_check / staging_test）按这个契约直接 `kubectl exec
runner cd /workspace/source/<repo> && make ...`。

但 sisyphus 没有任何 **预飞行合规 check**：

- 一个新 repo 进 `involved_repos` 列表后，dev_cross_check 第一次跑才会发现 target
  缺，错误形态是 `make: *** No rule to make target 'ci-lint'`，verifier-agent
  得人工识别成"基础设施缺失"而非业务 bug
- 两类返回（target 缺 vs 业务红）都退码 ≠ 0，sisyphus 没法在 stage 层区分；只能
  靠 staging_test stderr 文本做启发式（实际目前没做）

REQ-default-involved-repos-1777124541 起 `default_involved_repos=[phona/sisyphus]`
让 sisyphus 自托管单仓跑通了。**下一步要把 ttpos 业务双仓加进去**之前，先把
现状摸一遍：哪些 repo 已经合规、哪些缺、缺哪几条。

## 摘要（详见 audit-report.md）

**ttpos-server-go**（实际位置 `ZonEaseTech/ttpos-server-go`，默认分支 `release`）：

- ✅ 全 6 个 ttpos-ci 标准 target 都在（含 `BASE_REV` 增量 lint 写法对齐
  contract `golangci-lint run $${BASE_REV:+--new-from-rev=$$BASE_REV}`）
- ⚠️ `ci-build` 是 stub —— 只 `@echo`，注释说 "复用现有的构建逻辑"，没真跑（不影响
  sisyphus 三个机械 checker，但 ttpos-ci 的 build.yml 走这个 target）
- ⚠️ 默认分支 `release` 而非 `main`：sisyphus runner pod 计算 BASE_REV 的 chain
  是 `origin/main → origin/develop → origin/dev → empty`，**全部 miss** →
  BASE_REV=空 → ci-lint 全量扫（功能正确，只是慢）
- ✅ 有 `.github/workflows/dispatch.yml`，到 `phona/ttpos-ci` 的 `ci-go` 通路通

**ttpos-flutter**（实际位置 `ZonEaseTech/ttpos-flutter`，默认分支 `release`）：

- ❌ **repo 根目录没有 Makefile**。`apps/` 下 10 个 Flutter app 也没。构建走 melos
  + `scripts/*.dart`（Bun + Dart 自定义脚本链），跟 ttpos-ci Makefile 契约完全
  没对接
- ❌ ci-flutter.yml 调用的 `ci-env` / `ci-setup` / `ci-lint` / `ci-unit-test` **全部
  缺失**。其中 ci-env / ci-setup 在 workflow 里有 `|| true` 兜底；`ci-lint` /
  `ci-unit-test` **没有**兜底 —— 真要触发会立刻 `make: *** No rule` 红
- ❌ 仓内**没有 dispatch workflow**（只有 `commit-lint.yaml` /
  `trigger-artifact-builds.yaml` / `trigger-test-builds.yaml` 等），所以 ttpos-ci
  的 `ci-flutter` 现在根本**触发不到**。这意味着即便把 Makefile 写出来，PR 也不会
  跑 phona/ttpos-ci 那条 lint/unit-test pipeline
- 默认分支 `release`：sisyphus BASE_REV chain 同样 miss

**契约文档自身的不一致（可以顺手记下）**：

1. `phona/ttpos-ci/README.md` 标准说 ci-lint 读 **`BASE_REV` 环境变量**。
   但 `phona/ttpos-ci/.github/workflows/ci-flutter.yml` 实际写法是
   `make ci-lint BASE_REF="$BASE_REF"` —— 拼错成 `BASE_REF`（少一个 V）+ 用
   make-arg 而非 env。`ci-go.yml` 写对了（`env: BASE_REV: ${{ ... }}`）。
   单字符之差能让 flutter 那条永远全量 lint
2. `docs/integration-contracts.md` §1 "两类 repo 角色" 表格用
   `phona/ttpos-server-go` / `phona/ttpos-arch-lab` 做例子，但实际两个 repo
   都在 `ZonEaseTech/` 下。analyze-agent 看 doc 自己拼仓路径会 404
3. `docs/integration-contracts.md` §6 默认 dev-agent 的 `feat/REQ-id` 分支以
   `main` 为基（`gh pr list --head feat/REQ-29 --repo ...`）；两个业务 repo 实际
   default branch `release`。pr-ci-watch 的查询不依赖 base，但 BASE_REV
   merge-base 计算依赖

## 取舍

**为什么不直接顺手把 GAP 修了**（仓里加 Makefile / dispatch.yml 等）：

- 审计是分析层活；改业务 repo Makefile 是它仓的 PR + 它仓 owner 决策。从
  sisyphus 这边强推 Makefile 模板会越界（参考 CLAUDE.md "不抢 AI 决定权"，扩
  展到"不抢业务 repo owner 决定权"）
- ttpos-flutter 用 melos + dart scripts 这套自有体系，把它硬塞进 ttpos-ci
  Makefile 契约可能不是它团队最优解 —— 也许更好的路径是 ttpos-ci 加一个
  flutter melos-aware 的 dispatch path，或单独一份 `flutter-ci.yml`
  调 melos commands。这种方案选型应该在 onboarding REQ 阶段拍，不是审计 REQ
  顺手做
- ci-build stub 不影响 sisyphus 的三个机械 checker —— 是 ttpos-ci build.yml 才用
  到。修它不阻塞 sisyphus 跑这俩 repo

**为什么沉淀成 spec 而不只是 docs**：

`specs/business-repo-onboarding-audit/spec.md` 把这次审计的 5 条核对项变成
可复用 requirements。下次再有人提"接 phona/zplan 进 sisyphus"这样的 REQ，
analyze-agent 看这个 spec 就能直接拿 checklist 跑一遍 + 同样的 audit-report
形态产出。

## 后续 REQ 候选（不在本 REQ 范围）

1. `REQ-ttpos-server-go-default-branch-or-base-rev-fallback`：要么把
   server-go default branch 改 `main`，要么改 sisyphus BASE_REV 计算 chain
   加 `release` 兜底
2. `REQ-ttpos-flutter-make-or-melos-bridge`：在 flutter 仓加最小 Makefile
   wrapper，或在 phona/ttpos-ci 加 melos-native flutter pipeline
3. `REQ-ttpos-flutter-dispatch-yml`：补 dispatch.yml 让 ttpos-ci ci-flutter 真能跑
4. `REQ-fix-ci-flutter-base-rev-arg`：phona/ttpos-ci/.github/workflows/ci-flutter.yml
   把 `BASE_REF=` 改成 env `BASE_REV:`，拼写对齐 README
5. `REQ-fix-integration-contracts-md-org-paths`：docs 里 `phona/ttpos-*` 例子
   改 `ZonEaseTech/ttpos-*` 或改成纯占位 `<owner>/<repo>`
6. `REQ-add-base-rev-doc-for-non-main-default-branch`：integration-contracts.md
   §2.2 写明 "若仓 default branch 既非 main 也非 develop/dev，BASE_REV 会
   降级为全量；此为预期行为"

每个候选独立小型 REQ，都不依赖其他先做。

## 范围内做的事

- [x] 读 phona/ttpos-ci/README.md + ci-go.yml + ci-flutter.yml 三份做契约真值
- [x] 通过 GitHub REST API 拉两个业务 repo 的 Makefile + workflow 内容
- [x] 逐条对齐契约 vs 现状，写入 `audit-report.md`
- [x] 把审计核对项沉淀成 `specs/business-repo-onboarding-audit/spec.md` 复用
- [x] 列出 GAP 候选 follow-up REQ
