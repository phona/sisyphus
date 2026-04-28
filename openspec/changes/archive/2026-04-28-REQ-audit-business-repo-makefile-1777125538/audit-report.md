# ttpos-server-go + ttpos-flutter ↔ ttpos-ci 契约审计报告

**REQ**: REQ-audit-business-repo-makefile-1777125538
**审计日期**: 2026-04-25
**审计范围**: 两个业务 repo 的 Makefile（含 include 链）覆盖 ttpos-ci 标准
契约 + sisyphus 强约束的程度
**审计方式**: 只读，通过 GitHub REST API 拉源文件，不修改任何仓

## 1. 契约真值（合并后）

ttpos-ci 契约由两份文档共同定义，本审计取并集：

### 1.1 phona/ttpos-ci/README.md "业务仓库 CI 契约"

| Target | 必需 | 环境变量 | 说明 |
|---|---|---|---|
| `ci-env` | ✅ | — | 输出 `KEY=VALUE` 到 stdout |
| `ci-setup` | ✅ | — | 装依赖、备环境 |
| `ci-lint` | ✅ | `BASE_REV` | 读环境变量；空则全量 |
| `ci-unit-test` | ✅ | — | 覆盖率到 `coverage/` |
| `ci-integration-test` | ✅ | `BUILD_ID` | 覆盖率到 `coverage/` |
| `ci-build` | ✅ | `REF_NAME` | 构建产物 |

`ci-env` 输出 key：`GO_VERSION` / `FLUTTER_VERSION` / `NEEDS_DOCKER` / `NEEDS_SUBMODULES`

### 1.2 phona/sisyphus/docs/integration-contracts.md §2.1（机械 checker 子集）

sisyphus 的 dev_cross_check / staging_test 只调三条：

| Target | sisyphus 调用方 | 调用形态 |
|---|---|---|
| `ci-lint` | dev_cross_check (M15) | `cd /workspace/source/<repo> && BASE_REV=$(git merge-base HEAD origin/main) make ci-lint` |
| `ci-unit-test` | staging_test (M1) | 同上 path，`make ci-unit-test` |
| `ci-integration-test` | staging_test (M1) | 同上 path，`make ci-integration-test`（单仓内串行 unit→integration） |

注：sisyphus 的 BASE_REV 计算 chain（来自 §2.2）是
`origin/main → origin/develop → origin/dev → 空字符串`。

### 1.3 phona/ttpos-ci/.github/workflows/ ci-go.yml + ci-flutter.yml 实际调用差异

**ci-go.yml** 走完整链：`ci-env` → `ci-setup` → `ci-lint` (env `BASE_REV`) →
`ci-unit-test` → `ci-integration-test BUILD_ID=...` → `ci-build` → SonarQube。

**ci-flutter.yml** 只走 4 个：`ci-env` → `ci-setup` → `ci-lint BASE_REF="$BASE_REF"`
→ `ci-unit-test` → SonarQube。**没有** `ci-integration-test`，**没有** `ci-build`。

⚠️ ci-flutter.yml 里的 `BASE_REF=` 跟 README/Go 用的 `BASE_REV` 拼写不一致 ——
见 §4.1。

## 2. ttpos-server-go 审计

仓 URL：`https://github.com/ZonEaseTech/ttpos-server-go` （**注**：integration-contracts.md
里写的 `phona/ttpos-server-go` **不存在**，实际位置在 ZonEaseTech 组织下，私有）
默认分支：`release`

### 2.1 Makefile 结构

```
ttpos-server-go/
├── Makefile                          # root；业务 install/build/run/migrate 等
├── ttpos-scripts/
│   ├── help.mk                       # included
│   └── lint-ci-test.mk                # included；CI 标准 target 都在这里
└── ...
```

root Makefile 顶部：

```makefile
include ./ttpos-scripts/help.mk
include ./ttpos-scripts/lint-ci-test.mk
```

CI 标准 target 全在 `ttpos-scripts/lint-ci-test.mk` "CI 标准接口 — 供构建仓库调用"
小节，`.PHONY: ci-env ci-setup ci-lint ci-unit-test ci-integration-test ci-build`。

### 2.2 逐 target 对齐

| Target | 状态 | 备注 |
|---|---|---|
| `ci-env` | ✅ PASS | 输出 `GO_VERSION=1.23` / `NEEDS_DOCKER=true` / `NEEDS_SUBMODULES=true`，全 ttpos-ci 标准 key 都覆盖 |
| `ci-setup` | ✅ PASS | `cd main && go mod download` + `cd ttpos-bmp && go mod download` + 自动装 golangci-lint v1.62.2（按需） |
| `ci-lint` | ✅ PASS | `cd main && go vet ./...` 并发 `cd main && golangci-lint run $${BASE_REV:+--new-from-rev=$$BASE_REV}` —— BASE_REV 空字符串 shell 不展开 flag，等价全量；非空则增量。**写法跟 sisyphus contract §2.2 推荐写法逐字符一致** |
| `ci-unit-test` | ✅ PASS | Main + BMP 并发：`go test -short -v -count=1 -coverprofile=../coverage/unit.out` + bmp 同样。覆盖率到 `coverage/unit.out` + `coverage/bmp-unit.out`，对齐契约"输出到 coverage/" |
| `ci-integration-test` | ✅ PASS | 用 `BUILD_ID`（缺省 `$(shell date +%s)`），并发跑 `test-main-local` + `test-bmp-local`，每路自己 docker compose 起 stack。失败传播：`fail=0; ... wait $$pid1 \|\| fail=1; ... exit $$fail` |
| `ci-build` | ⚠️ STUB | 仅 `@echo "Building Docker images for $(REF_NAME)..."` + 注释 `# 复用现有的构建逻辑`。**没有真 docker build / push**。phona/ttpos-ci 的 `build.yml` 走这个 target —— 当前会"成功"但不产物 |

### 2.3 sisyphus 强约束侧的额外检查

| 检查项 | 状态 | 备注 |
|---|---|---|
| `ci-lint` 读 `BASE_REV` env | ✅ PASS | shell `$${BASE_REV:+...}` 形态完全对齐契约 §2.2 |
| `ci-unit-test` 退码作为 sole signal | ✅ PASS | 两路 fail 任一 → exit 1 |
| `ci-integration-test` 退码同上 | ✅ PASS | 同上模式 |
| 单仓内 unit→integration 串行 OK | ✅ PASS | sisyphus 这边 staging_test 串行调，不依赖 Makefile 内部并发；Makefile 把 main+bmp 并发是仓内事 |
| `feat/<REQ>` 分支 PR 能被 pr-ci-watch 找到 | ⚠️ WARN | 默认分支 `release`，不是 `main`。如果 PR base 是 release：pr-ci-watch 按 `gh pr list --head feat/REQ-x` 查能命中（不依赖 base），但 Makefile/CI 流的 `git merge-base HEAD origin/main` 会 fallthrough |
| BASE_REV 计算成功 | ❌ GAP | 默认分支 `release`，sisyphus chain `main → develop → dev` 全 miss → BASE_REV=空 → ci-lint 全量扫（功能正确，性能问题；contract §2.2 没正式承认这个降级） |

### 2.4 结论

**ttpos-server-go 对 sisyphus 三大机械 checker 是 ready 状态**。dev_cross_check
+ staging_test 直接 `kubectl exec runner cd /workspace/source/ttpos-server-go &&
make ci-lint` / `ci-unit-test` / `ci-integration-test` 都会跑起来。

唯一要注意的是默认分支不是 main，BASE_REV 永远空 → ci-lint 全量。这不是 BUG，
但应该在 contract 文档里注明（见 §4 doc 不一致）。

`ci-build` stub 不阻塞 sisyphus，但**会让 phona/ttpos-ci 的 build.yml 当成成功
但其实没产物**。建议独立 follow-up REQ 修。

## 3. ttpos-flutter 审计

仓 URL：`https://github.com/ZonEaseTech/ttpos-flutter` （同样 integration-contracts.md
写的 `phona/ttpos-flutter` 不存在）
默认分支：`release`

### 3.1 Makefile 结构

❌ **repo 根目录没有 Makefile 文件**。

repo 根目录文件清单（在 `release` 分支）：

```
.agents.example      .claude/             .cursor/
.cursorignore        .env.example         .fvmrc
.github/             .gitignore           .husky/
.mcp.json            .semgrep/            .semgrepignore
.serena/             .vscode/             AGENTS.md
CLAUDE.md            COMMAND_GUIDE.md     DOCKER_BUILD.md
Dockerfile.build     Dockerfile.member    Dockerfile.menu
Dockerfile.mobile    README.md            SECURITY_AUDIT_REPORT.md
apps/                bun.lockb            cmd
compax.keystore      docker-compose.dev.yml
docker-compose.production.yml             docker-nginx.conf
docker/              docs/                package.json
packages/            pubspec.lock         pubspec.yaml
scripts/
```

`scripts/` 下是一组 Dart 脚本（`build_android.dart` / `build_ios.dart` /
`build_mac.dart` / `build_web.dart` / `pre_commit.dart` / `clean.dart` / 等），
和一个独立 `cmd` 文件（2.4 KB，可能是 dispatcher）。

构建链：

- `pubspec.yaml`：melos workspace（含 `apps/pos`, `apps/assistant`, `apps/shop`,
  `apps/kds`, `apps/member`, `apps/menu` 等 10 个 sub-app），dev_dependencies
  包括 melos / husky / args / archive
- `package.json`：仅 `ofetch` / `prisma` / `qs` 三个运行时依赖 + bun types。看
  起来是给 prisma + Bun 跑 schema 工具

→ flutter 这套用 melos + dart scripts + bun，**完全不走 Make**。

### 3.2 逐 target 对齐

ci-flutter.yml 实际只调 4 个 target，全部状态：

| Target | 状态 | 备注 |
|---|---|---|
| `ci-env` | ❌ GAP | 无 Makefile → 无 target。ci-flutter.yml 用 `make ci-env > /dev/null 2>&1; if ...; then make ci-env >> $GITHUB_OUTPUT; fi` 兜底（见 init job），失败静默不阻塞，但 init outputs `flutter_version` / `needs_submodules` 都会是空。后续 step `subosito/flutter-action@v2` 收到空 `flutter-version` → 走 stable —— 行为模糊 |
| `ci-setup` | ❌ GAP | 同上无 target。workflow `make ci-setup \|\| true` 兜底 |
| `ci-lint` | ❌ GAP，**会真红** | 无兜底（workflow 写 `run: make ci-lint BASE_REF="$BASE_REF"` 直接调）→ `make: *** No rule to make target 'ci-lint'. Stop.` 退码 2 |
| `ci-unit-test` | ❌ GAP，**会真红** | 同上无兜底（workflow 写 `run: make ci-unit-test`）→ make 报 no rule |

ci-flutter.yml **不调** `ci-integration-test` / `ci-build`，所以这俩缺也不影响
phona/ttpos-ci 走 ci-flutter 通路。**但 sisyphus 的 staging_test 一定调
`ci-integration-test`** —— 对 sisyphus 是 GAP。

### 3.3 sisyphus 强约束侧

| 检查项 | 状态 |
|---|---|
| `ci-lint` 存在 | ❌ GAP |
| `ci-unit-test` 存在 | ❌ GAP |
| `ci-integration-test` 存在 | ❌ GAP |
| `BASE_REV` env 处理 | ❌ N/A（target 不存在） |

**所有 3 条 sisyphus 机械 checker 都会立即 `make: *** No rule` 红**。flutter
这仓现在**完全不能进 sisyphus 的 involved_repos**。

### 3.4 触发链 GAP

phona/ttpos-ci 的 ci-flutter workflow 是 `repository_dispatch` 触发，需要业务仓
有 `dispatch.yml` 发 `event_type: ci-flutter` 的 dispatch。

ttpos-flutter `.github/workflows/` 下的 workflows：

```
claude-code-review.yml
claude.yml
commit-lint.yaml
issue-router.yml
project-automation.yml
trigger-artifact-builds.yaml
trigger-test-builds.yaml
```

❌ **没有 `dispatch.yml`**。ci-flutter pipeline 当前**根本无法被触发**。

参考 ttpos-server-go 同位置有 `dispatch.yml`（确认通路是通的）。

### 3.5 结论

**ttpos-flutter 对 ttpos-ci + sisyphus 双重契约都 100% miss**。直接接 sisyphus
会让 dev_cross_check / staging_test 立即红；接 ttpos-ci 也接不通（dispatch
缺失）。

修复路径有选择，不是 sisyphus 单方面拍：

- **路径 A**: 在 flutter 仓加根 Makefile，把 melos / dart scripts 包成
  ttpos-ci 标准 target（`ci-lint` 转 `melos run lint`、`ci-unit-test` 转
  `melos test`、`ci-integration-test` 待定 / 可暂时空 OK）。最小侵入
- **路径 B**: 在 ttpos-ci 加一份 `ci-flutter-melos.yml`，原生用 melos
  commands，绕过 Makefile。需要业务 repo + ttpos-ci 双改

`docs/integration-contracts.md` 当前没规定 melos-native repo 的 onboarding
路径，需要先在 ttpos-ci 团队 + sisyphus 团队对齐再起 follow-up REQ。

## 4. 文档不一致（顺手记录）

### 4.1 BASE_REV vs BASE_REF 拼写不一致（phona/ttpos-ci）

| 来源 | 写法 |
|---|---|
| `phona/ttpos-ci/README.md` "环境变量传递" 表格 | `BASE_REV` |
| `phona/ttpos-ci/.github/workflows/ci-go.yml` | env `BASE_REV: ${{ ... }}` ✅ 对齐 README |
| `phona/ttpos-ci/.github/workflows/ci-flutter.yml` | `make ci-lint BASE_REF="$BASE_REF"` ❌ 拼成 BASE_REF（少一个 V）+ 用 make-arg 而非 env |

`BASE_REF` 是 GitHub Actions 内置的 ref 名（不是 SHA），跟 ttpos-ci 契约的
"merge-base SHA"语义完全不同。即使 flutter 仓将来加了 `ci-lint` target 读
`$$BASE_REV`，这条 workflow 也传不过去。

修法：ci-flutter.yml lint job 改成

```yaml
- name: Compute merge base
  ...  # 学 ci-go.yml 的 base 计算
- name: Lint
  env:
    BASE_REV: ${{ needs.init.outputs.base_rev }}
  run: make ci-lint
```

### 4.2 phona/sisyphus/docs/integration-contracts.md 例子组织错位

§1 "两类 repo 角色" 表格：

| 角色 | 例子 |
|---|---|
| source repo | `phona/ttpos-server-go`、`phona/ubox-crosser` |
| integration repo | `phona/ttpos-arch-lab` |

实测：

- `phona/ttpos-server-go` → 404
- `phona/ubox-crosser` → ✅ 存在（public）
- `phona/ttpos-arch-lab` → 404
- 实际位置：`ZonEaseTech/ttpos-server-go`（私有）+ `ZonEaseTech/ttpos-arch-lab`（私有）

§7 排查清单 / §8 等下文还有 `phona/ttpos-server-go` 的引用。

修法：要么改成 `ZonEaseTech/ttpos-server-go` 等真值（前提：sisyphus 团队读得到
ZonEaseTech），要么改成中性占位 `<owner>/<repo>` + 一句"业务实际位置见
sisyphus admin"。

### 4.3 默认分支非 main 时 BASE_REV 行为未文档化

`docs/integration-contracts.md` §2.2 BASE_REV 计算 chain：

```bash
base_rev=$(git merge-base HEAD origin/main 2>/dev/null \
        || git merge-base HEAD origin/develop 2>/dev/null \
        || git merge-base HEAD origin/dev 2>/dev/null \
        || echo "")
```

ttpos-server-go / ttpos-flutter 默认 `release`，三条全 fall through → BASE_REV
为空 → ci-lint 全量扫。这是预期行为（空 BASE_REV 等价全量），但文档没明说"非
main/develop/dev 默认分支会自动降级全量"。

修法：在 §2.2 加一句注：

> 若 source repo default branch 既非 main / develop / dev，BASE_REV 会降级
> 为空字符串，触发 ci-lint 全量扫描；功能正确，性能稍差，可接受。

### 4.4 整体观察

这三条 doc 不一致都是小修，但散在两个 repo 的不同文件里，建议合一个独立小型
REQ 一次性收 —— 不混进任何业务仓的功能 PR。

## 5. 审计结果概览（一表）

| 维度 | ttpos-server-go | ttpos-flutter |
|---|---|---|
| Makefile 存在 | ✅ root + included `.mk` | ❌ 无 |
| `ci-env` | ✅ | ❌ |
| `ci-setup` | ✅ | ❌ |
| `ci-lint`（含 BASE_REV） | ✅ | ❌ |
| `ci-unit-test` | ✅ | ❌ |
| `ci-integration-test` | ✅ | ❌ |
| `ci-build` | ⚠️ stub | ❌ |
| `dispatch.yml` 到 ttpos-ci | ✅ | ❌ |
| 默认分支 == main | ❌（release） | ❌（release） |
| BASE_REV merge-base 命中 | ❌（chain miss） | ❌（无 ci-lint） |
| **sisyphus 三大机械 checker ready** | ✅ | ❌ |
| **phona/ttpos-ci pipeline 通路** | ✅ | ❌ |

## 6. 后续 REQ 候选（不在本 REQ 范围）

按优先级排：

1. **REQ-fix-integration-contracts-md-org-paths**（doc，最小，无依赖）—— 把
   sisyphus docs 里 `phona/ttpos-*` 改 `ZonEaseTech/ttpos-*` 或占位
2. **REQ-add-base-rev-doc-for-non-main-default-branch**（doc）—— §2.2 加非
   主流默认分支的降级说明
3. **REQ-fix-ci-flutter-base-rev-arg**（phona/ttpos-ci 仓）—— `BASE_REF=` 改
   `BASE_REV:` env，对齐 ci-go 写法
4. **REQ-ttpos-server-go-ci-build-real-impl**（业务仓 PR）—— 把 stub 换成
   真 docker build/push（仅影响 phona/ttpos-ci build.yml，不影响 sisyphus）
5. **REQ-ttpos-flutter-onboarding-strategy**（设计 REQ）—— 决定路径 A
   （Makefile wrapper）vs 路径 B（ttpos-ci melos-native pipeline），出
   spec
6. **REQ-ttpos-flutter-onboarding-impl**（依赖 5）—— 落地选定方案
7. **REQ-ttpos-server-go-default-branch-or-base-rev-fallback**（小，可拖）——
   如果 release 分支决策长期保持，sisyphus BASE_REV chain 加 release
   兜底；否则把 server-go default 改 main

每条 REQ 都独立可起，互相不阻塞。

## 7. 数据来源（reproducibility）

所有原文件通过 GitHub REST API 读取（read-only），关键 endpoint：

- `gh api repos/phona/ttpos-ci/contents/README.md`
- `gh api repos/phona/ttpos-ci/contents/.github/workflows/ci-go.yml`
- `gh api repos/phona/ttpos-ci/contents/.github/workflows/ci-flutter.yml`
- `gh api repos/ZonEaseTech/ttpos-server-go/contents/Makefile`
- `gh api repos/ZonEaseTech/ttpos-server-go/contents/ttpos-scripts/lint-ci-test.mk`
- `gh api repos/ZonEaseTech/ttpos-server-go/contents/.github/workflows`
- `gh api 'repos/ZonEaseTech/ttpos-flutter/contents?ref=release'`
- `gh api 'repos/ZonEaseTech/ttpos-flutter/contents/scripts?ref=release'`
- `gh api 'repos/ZonEaseTech/ttpos-flutter/contents/.github/workflows?ref=release'`

复审时可重跑这一组 + diff 输出。
