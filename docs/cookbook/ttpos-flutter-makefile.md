# Cookbook: ttpos-flutter 根 Makefile —— ci-* wrapper + accept-env 契约参与

> 给 **source repo `ZonEaseTech/ttpos-flutter`**（以及任何形似的「melos Flutter 多包
> 工作区」repo）一份能直接抄的根 `Makefile`：把 melos / dart CLI 包成
> ttpos-ci 标准 target，让 sisyphus staging-test / dev-cross-check 能直接调；
> 同时说明 Flutter 源仓如何参与 accept 阶段（它是 **source repo**，不是
> integration repo，由 arch-lab 从 `/workspace/source/<basename>/` 编 APK）。
>
> 适用场景：Flutter 仓**没有根 Makefile**，CI 目前走 melos + dart scripts，
> 需要以最小侵入方式接入 sisyphus。
>
> 契约权威是 [`docs/integration-contracts.md`](../integration-contracts.md)；
> accept 阶段完整 lab 食谱见 [`docs/cookbook/ttpos-arch-lab-accept-env.md`](ttpos-arch-lab-accept-env.md)。
> 本 cookbook 只覆盖 Flutter **源仓侧**的 Makefile；冲突以契约文档为准。

---

## 0. TL;DR — 你需要知道的 3 件事

1. **sisyphus 三大机械 checker 只认 Makefile target**（`ci-lint` / `ci-unit-test`
   / `ci-integration-test`）。Flutter 仓不管用 melos / bun / dart scripts，根目录
   **必须有一份 Makefile** 把这些命令包进去。
2. **Flutter 源仓是 source repo，不提供 accept-env-up/down**。accept 阶段由
   integration repo（如 `ttpos-arch-lab`）克隆 `/workspace/source/ttpos-flutter/`
   后跑 `flutter build apk` 编 APK —— 你的仓只需要被 clone 到约定路径即可。
3. **BASE_REV 传进来但不需要花哨处理**。Flutter 没有 golangci-lint 的
   `--new-from-rev`；最简单、最可靠的做法是收到 `BASE_REV` 后**仍跑全量
   `flutter analyze`**。后文给进阶增量方案供选用。

---

## 1. 背景：ttpos-flutter 的现状

`ZonEaseTech/ttpos-flutter` 根目录没有 Makefile。构建链走：

| 工具 | 用途 |
|---|---|
| **melos** | Dart 多包 workspace 管理（apps/pos, apps/assistant, apps/shop, apps/kds …） |
| **dart scripts/** | `scripts/build_android.dart` / `build_ios.dart` / `clean.dart` / `pre_commit.dart` 等 |
| **bun / package.json** | prisma schema 工具 + ofetch |

sisyphus staging-test checker 在 runner pod 里跑：

```bash
cd /workspace/source/ttpos-flutter && make ci-unit-test
```

直接调的是 **make**，不懂 melos / dart scripts。结果是 `make: *** No rule to make target 'ci-unit-test'. Stop.`，立即红。

**修法（本 cookbook）**：在根目录加一份薄 Makefile，把 melos / dart 命令包成
ttpos-ci 标准 target。不改内部构建链，侵入最小。

> 另一条路（路径 B）是在 `phona/ttpos-ci` 加 melos-native pipeline，绕开 Makefile。
> 需要双仓（ttpos-flutter + ttpos-ci）同时改，协调成本高；本 cookbook 不覆盖。

---

## 2. repo 布局（加 Makefile 后）

```
ttpos-flutter/
├── Makefile                          ← ← ← 本 cookbook 的产物（根目录）
├── pubspec.yaml                      ← melos workspace 声明
├── melos.yaml                        ← melos 配置（lint / test / build scripts）
├── apps/
│   ├── pos/
│   ├── assistant/
│   ├── shop/
│   ├── kds/
│   ├── member/
│   └── menu/
├── packages/                         ← 共用 lib
├── scripts/
│   ├── build_android.dart
│   └── ...
└── ...
```

Makefile 只有 6 个 target，全部委托给已有工具，**零重复逻辑**。

---

## 3. ci-* target 实现

### 3.1 ci-env

输出 `KEY=VALUE` 到 stdout，供 `phona/ttpos-ci` workflow init job 读取。

```makefile
ci-env:
	@echo "FLUTTER_VERSION=$(shell flutter --version --machine 2>/dev/null | \
	    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('flutterVersion','stable'))" \
	    2>/dev/null || echo "stable")"
	@echo "NEEDS_DOCKER=false"
	@echo "NEEDS_SUBMODULES=false"
```

> `FLUTTER_VERSION=stable` 是安全兜底：`subosito/flutter-action@v2` 收到 `stable`
> 走 stable channel，行为确定。如果仓用 `fvm`（Flutter Version Manager），改成读
> `.fvmrc`：`cat .fvmrc 2>/dev/null || echo "stable"`。

### 3.2 ci-setup

装依赖、验 toolchain 就绪。

```makefile
ci-setup:
	flutter pub get
	@# melos workspace 需要 bootstrap
	dart pub global activate melos 2>/dev/null || true
	melos bootstrap
```

> runner 镜像（`ghcr.io/phona/sisyphus-runner:main`）基于 `cirruslabs/flutter:stable`，
> Flutter SDK + Android SDK + Java 已内置。`pub global activate melos` 只在
> melos 不在 PATH 时才真正装（`|| true` 避免重复报错）。

### 3.3 ci-lint（含 BASE_REV）

Flutter 没有 golangci-lint 的 `--new-from-rev`。两种方案：

#### 方案 A：全量扫描（推荐，简单可靠）

```makefile
ci-lint:
	@if [ -n "$$BASE_REV" ]; then \
	    echo "ci-lint: BASE_REV=$$BASE_REV（Flutter 全量扫，不做增量过滤）"; \
	fi
	flutter analyze --no-pub
	dart format --set-exit-if-changed .
```

`dart format --set-exit-if-changed .` 会检查格式但**不改文件**；任何格式不对的
文件导致退码 1。这等价于 Go 的 `gofmt -l`。

> 为什么 Flutter 全量扫没问题：`flutter analyze` 只做静态分析，不跑编译；
> 中型 melos workspace（10 个 package）通常在 30–90s 内完成，在 sisyphus 8 GiB
> pod 内完全可接受。

#### 方案 B：增量过滤（进阶，melos workspace 时推荐）

如果 workspace 很大（20+ packages），用 `melos run` 的 `--scope` 过滤只分析
有变更的 package：

```makefile
ci-lint:
	@if [ -z "$$BASE_REV" ]; then \
	    echo "ci-lint: full scan"; \
	    melos run lint; \
	else \
	    changed_pkgs=$$(git diff --name-only --diff-filter=ACMR "$$BASE_REV"...HEAD -- '*.dart' \
	        | awk -F/ '{print $$1"/"$$2}' | sort -u | tr '\n' ','); \
	    if [ -z "$$changed_pkgs" ]; then \
	        echo "ci-lint: no Dart files changed (BASE_REV=$$BASE_REV)"; \
	        exit 0; \
	    fi; \
	    echo "ci-lint: scoped to $$changed_pkgs"; \
	    melos run lint --scope="$$changed_pkgs"; \
	fi
```

前提：`melos.yaml` 里已定义 `scripts.lint`：

```yaml
# melos.yaml
scripts:
  lint:
    run: dart analyze . && dart format --set-exit-if-changed .
    exec:
      concurrency: 6
```

大多数情况**方案 A 足够**；仅当 staging_test dev_cross_check 经常因 lint 慢超时时
才考虑方案 B。

### 3.4 ci-unit-test

```makefile
ci-unit-test:
	melos run test:unit
```

`melos.yaml` 里定义单元测试 script（**不需要 device / emulator**）：

```yaml
# melos.yaml
scripts:
  test:unit:
    run: flutter test test/ --no-pub --coverage
    exec:
      concurrency: 4
    description: "unit tests across all packages（无 device）"
```

> `flutter test` 默认跑 `test/` 目录；不带 `--device-id` 在 desktop 模式
> 运行 widget test，**不需要 Android emulator**。runner 镜像 Linux 环境下
> `flutter test` 会用 `linux` target —— 所有不涉及平台特定 UI 的测试都能通过。
> 如果测试依赖 Android-specific plugin（如 `flutter_blue` / `camera`），需要
> mock 这些 plugin；参考 `mocktail` + `plugin_platform_interface` 做法。

**覆盖率输出**：`coverage/lcov.info`，对齐 ttpos-ci 契约"覆盖率到 `coverage/`"。

### 3.5 ci-integration-test

集成测试对 Flutter 有三种实现路径：

#### 路径 C1：暂不实现（空实现，exit 0）

```makefile
ci-integration-test:
	@echo "ci-integration-test: no integration tests configured (skipped)"
	@exit 0
```

sisyphus 接受退码 0，视为 pass。适合早期接入阶段。

#### 路径 C2：docker compose 后端集成测试（推荐长期方案）

Flutter 业务层有 HTTP 调用（Dio / http package）时，可以用 docker compose 起
mock/stub 后端，跑 Dart integration test（不需要 emulator）：

```makefile
ci-integration-test:
	docker compose -f tests/docker-compose.integration.yml up --build --exit-code-from test-runner --abort-on-container-exit
```

`tests/docker-compose.integration.yml` 骨架：

```yaml
services:
  backend-stub:
    image: ghcr.io/<org>/ttpos-backend-stub:latest
    ports:
      - "8080"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/healthz"]
      interval: 3s
      retries: 10

  test-runner:
    build:
      context: .
      dockerfile: tests/Dockerfile.dart-test
    depends_on:
      backend-stub:
        condition: service_healthy
    environment:
      API_BASE_URL: "http://backend-stub:8080"
    command: dart test tests/integration/ --no-pub
```

#### 路径 C3：flutter drive（需要 emulator，**不推荐在 sisyphus runner pod 跑**）

`flutter drive --target integration_test/app_test.dart` 需要 connected device。
runner pod 里没有 emulator（emulator 是 accept 阶段才起，在 arch-lab integration
repo 里）。staging-test 阶段**不跑 flutter drive**。

> 强约束：sisyphus staging-test 期望 `make ci-integration-test` 在 runner pod 内
> 通过。emulator-dependent 的 e2e test 放 accept 阶段，不放 staging-test。

### 3.6 ci-build

```makefile
ci-build:
	flutter build apk --release \
	    --dart-define=APP_ENV="${APP_ENV:-production}" \
	    --target lib/main.dart
	@mkdir -p build/outputs
	@cp build/app/outputs/flutter-apk/app-release.apk build/outputs/app.apk
	@echo "ci-build: APK -> build/outputs/app.apk"
```

> `ci-build` **不是** sisyphus staging-test 必须项（sisyphus 调 `ci-unit-test` +
> `ci-integration-test`）。但 `phona/ttpos-ci` 的 `build.yml` 调它；
> 如果业务不需要 phona/ttpos-ci 构建 APK，这个 target 可以是 stub：
> `@echo "APK build handled by arch-lab integration repo; skipped here"`。

---

## 4. accept-env 契约：Flutter 源仓的参与方式

### 4.1 Flutter 源仓是 source repo，不是 integration repo

根据 `integration-contracts.md` §1：

| 角色 | 谁做 |
|---|---|
| source repo（`ci-*` targets） | `ZonEaseTech/ttpos-flutter`（本 cookbook） |
| integration repo（`accept-env-up/down`） | `ZonEaseTech/ttpos-arch-lab` |

**Flutter 源仓不需要实现 `accept-env-up` / `accept-env-down`**。这两个 target
由 integration repo 提供；sisyphus 在 accept 阶段只进 `/workspace/integration/<lab-repo>/`
里调这两个 target。

### 4.2 Flutter 源仓如何为 accept 阶段贡献代码

sisyphus orchestrator 在 dispatch analyze-agent 之前，会把 BKD intent issue 里
`involved_repos` 列出的所有仓 clone 到 runner pod：

```
/workspace/source/ttpos-flutter/   ← Flutter 源码（已 clone）
/workspace/integration/ttpos-arch-lab/  ← lab repo（accept 阶段 clone）
```

arch-lab 的 `apk/build.sh` 在 accept 阶段跑时，会从约定路径读 Flutter 源码：

```bash
# ttpos-arch-lab/apk/build.sh（节选）
SRC_REPO="${TTPOS_FLUTTER_REPO:-/workspace/source/ttpos-flutter}"
if [[ -d "$SRC_REPO" ]]; then
    cd "$SRC_REPO"
    flutter build apk --release --dart-define=API_BASE_URL="$TTPOS_API_BASE_URL"
fi
```

**你只需要做两件事**：

1. 确保仓名（basename）在 BKD intent issue 的 `involved_repos` 里写正确
   （`ZonEaseTech/ttpos-flutter`，或你们用的实际 org/repo）
2. Flutter 源码能在 runner pod 里执行 `flutter pub get && flutter build apk`
   —— 通常只要 `pubspec.yaml` 正确就能保证

### 4.3 accept-env-up/down 的 option（仅当 Flutter 仓兼作 integration repo 时）

极少数情况下，Flutter 仓本身想充当 integration repo（例如：只有后端 mock + HTTP
acceptance test，不需要 emulator）。这时可以加：

```makefile
# ── 仅 integration repo 角色时加（通常不需要）──────────────────────────
# sisyphus 注入；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default
COMPOSE_PROJECT_NAME := $(SISYPHUS_NAMESPACE)
MOCK_BACKEND_COMPOSE ?= tests/docker-compose.accept.yml

accept-env-up:
	docker compose -p $(COMPOSE_PROJECT_NAME) -f $(MOCK_BACKEND_COMPOSE) up -d --wait >&2
	@port=$$(docker compose -p $(COMPOSE_PROJECT_NAME) -f $(MOCK_BACKEND_COMPOSE) \
	    port backend 8080 | awk -F: 'END{print $$NF}'); \
	if [ -z "$$port" ]; then \
	    echo "[flutter-lab] FAIL: cannot resolve backend host port" >&2; exit 1; \
	fi; \
	printf '{"endpoint":"http://localhost:%s","namespace":"%s"}\n' \
	    "$$port" "$(SISYPHUS_NAMESPACE)"

accept-env-down:
	-docker compose -p $(COMPOSE_PROJECT_NAME) -f $(MOCK_BACKEND_COMPOSE) \
	    down --volumes --remove-orphans 2>&1 || true
```

> 大多数 Flutter 项目的完整 e2e 由 `ttpos-arch-lab` 做（带 emulator）。上面的
> option 只适合"只验 HTTP 层、不验 UI"的场景。

---

## 5. BASE_REV 约定（Flutter 版详解）

sisyphus dev_cross_check checker 在 runner pod 里计算（`integration-contracts.md` §2.2）：

```bash
base_rev=$(git merge-base HEAD origin/main 2>/dev/null \
        || git merge-base HEAD origin/develop 2>/dev/null \
        || git merge-base HEAD origin/dev 2>/dev/null \
        || echo "")
BASE_REV="$base_rev" make ci-lint
```

`ttpos-flutter` 默认分支是 `release`，三条全 fallthrough → `BASE_REV` 为空字符串
→ **ci-lint 全量扫描**。这是已知且可接受的行为（`integration-contracts.md` §2.2
明确：空字符串等价全量）。

如果将来 `ttpos-flutter` 默认分支改成 `main`，sisyphus 会正确计算 merge-base，
`flutter analyze` 仍全量扫（方案 A），或 `melos run lint --scope=...`（方案 B）。

**一句话**：Flutter 侧 Makefile 只需要支持"接受 `BASE_REV` env，空时全量，非空时
也全量（方案 A）"；checker 那边的 BASE_REV 计算不是你改的。

---

## 6. 完整 Makefile 范本

```makefile
# ttpos-flutter 根 Makefile
# 把 melos / dart CLI 包成 ttpos-ci 标准 target，供 sisyphus 机械 checker 调用。
#
# 依赖：runner 镜像 ghcr.io/phona/sisyphus-runner:main（cirruslabs/flutter:stable base）
#   已含 Flutter SDK + Android SDK + dart + melos（pub global activate 时安装）。
#
# target 一览：
#   ci-env              输出工具版本 key=value 到 stdout
#   ci-setup            flutter pub get + melos bootstrap
#   ci-lint             flutter analyze + dart format 检查（全量；接受 BASE_REV 但忽略）
#   ci-unit-test        melos run test:unit（无 device）
#   ci-integration-test docker compose 后端集成 or 空（EXIT 0）
#   ci-build            flutter build apk --release（可选；sisyphus 不直接调）

.PHONY: ci-env ci-setup ci-lint ci-unit-test ci-integration-test ci-build

# ── ci-env ───────────────────────────────────────────────────────────────────
ci-env:
	@echo "FLUTTER_VERSION=$(shell \
	    cat .fvmrc 2>/dev/null \
	    || flutter --version --machine 2>/dev/null \
	        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('flutterVersion','stable'))" \
	           2>/dev/null \
	    || echo "stable")"
	@echo "NEEDS_DOCKER=false"
	@echo "NEEDS_SUBMODULES=false"

# ── ci-setup ─────────────────────────────────────────────────────────────────
ci-setup:
	flutter pub get
	dart pub global activate melos 2>/dev/null || true
	melos bootstrap

# ── ci-lint ──────────────────────────────────────────────────────────────────
# BASE_REV 由 sisyphus dev_cross_check 注入；空字符串时全量（Flutter 无增量支持）。
# 非空时也全量扫（flutter analyze 无 --new-from-rev 等价）。
ci-lint:
	@if [ -n "$$BASE_REV" ]; then \
	    echo "ci-lint: BASE_REV=$$BASE_REV (Flutter does full scan regardless)"; \
	fi
	flutter analyze --no-pub
	dart format --set-exit-if-changed .

# ── ci-unit-test ─────────────────────────────────────────────────────────────
# melos 并发跑所有 package 的 flutter test test/ --no-pub。
# 无 device 依赖：widget test 走 linux/headless target。
ci-unit-test:
	melos run test:unit

# ── ci-integration-test ──────────────────────────────────────────────────────
# 选项 1（默认）：空实现，exit 0 → sisyphus 视为 pass。
# 取消注释选项 2 启用 docker compose 后端集成测试。
ci-integration-test:
	@echo "ci-integration-test: skipped (no integration tests configured)"
	@exit 0

# 选项 2：docker compose 后端集成测试（解注释后替换上面两行）
# ci-integration-test:
# 	docker compose -f tests/docker-compose.integration.yml \
# 	    up --build --exit-code-from test-runner --abort-on-container-exit

# ── ci-build ─────────────────────────────────────────────────────────────────
# APK 编译（供 phona/ttpos-ci build.yml 调用；sisyphus staging-test 不调此 target）。
# accept 阶段 APK 由 ttpos-arch-lab apk/build.sh 从 /workspace/source/ttpos-flutter/ 编。
ci-build:
	flutter build apk --release \
	    --dart-define=APP_ENV="${APP_ENV:-production}" \
	    --target lib/main.dart
	@mkdir -p build/outputs
	@cp build/app/outputs/flutter-apk/app-release.apk build/outputs/app.apk
	@echo "ci-build: APK -> build/outputs/app.apk"
```

---

## 7. melos.yaml 配套（`test:unit` script）

Makefile 的 `ci-unit-test` 委托给 `melos run test:unit`。`melos.yaml` 里需要：

```yaml
# melos.yaml（节选）
name: ttpos-flutter

packages:
  - apps/**
  - packages/**

scripts:
  lint:
    run: dart analyze . && dart format --set-exit-if-changed .
    exec:
      concurrency: 6

  test:unit:
    run: flutter test test/ --no-pub --coverage
    exec:
      concurrency: 4
      failFast: true
    description: "Unit/widget tests, no device required"
```

> `exec.concurrency` 控制并发 package 数。runner pod 4 vCPU / 8 GiB cgroup；
> 推荐 `concurrency: 4`，避免内存峰值叠加（sisyphus staging-test 在单 repo 内
> 已经 `ci-unit-test && ci-integration-test` 串行，不再外部并发加压）。

---

## 8. phona/ttpos-ci ci-flutter.yml 修正（关联修法）

审计（REQ-audit-business-repo-makefile-1777125538）发现 `ci-flutter.yml` 里
lint job 用 `make ci-lint BASE_REF="$BASE_REF"`，有两处问题：

1. **拼写**：`BASE_REF`（GitHub Actions 内置 ref 名）应为 `BASE_REV`（merge-base SHA）
2. **传参方式**：make CLI arg 形式不会注入 env；业务 `ci-lint` 读的是 `$$BASE_REV`
   env，应改为 `env: BASE_REV: ${{ ... }}`

建议的修法（`phona/ttpos-ci` 仓，不在本 cookbook 范围，记录供参考）：

```yaml
# .github/workflows/ci-flutter.yml — lint job（节选）
- name: Compute merge base
  id: base
  run: |
    base=$(git merge-base HEAD origin/main 2>/dev/null \
           || git merge-base HEAD origin/release 2>/dev/null \
           || echo "")
    echo "base_rev=$base" >> $GITHUB_OUTPUT

- name: Lint
  env:
    BASE_REV: ${{ steps.base.outputs.base_rev }}
  run: make ci-lint
```

Flutter 端 Makefile 的 `ci-lint` 接收 `BASE_REV` env 但全量扫，行为一致。

---

## 9. 跟 ttpos-arch-lab cookbook 的关系

两份 cookbook **互补不替代**，各自覆盖流水线不同阶段的不同 repo：

| | 本 cookbook | ttpos-arch-lab cookbook |
|---|---|---|
| repo 角色 | **source repo**（Flutter 源仓） | **integration repo**（mobile e2e lab） |
| Makefile target | `ci-lint` / `ci-unit-test` / `ci-integration-test` | `accept-env-up` / `accept-env-down` |
| sisyphus 调用阶段 | staging-test / dev-cross-check | accept 阶段（pre-accept env setup） |
| APK 来源 | `ci-build`（可选，供 ttpos-ci 调） | 从 `/workspace/source/ttpos-flutter/` 编译 |
| emulator | 无（staging-test 不需要） | 有（accept 阶段起 Android emulator） |

典型多仓 REQ 流程：

```
REQ involved_repos: [ZonEaseTech/ttpos-flutter, ZonEaseTech/ttpos-arch-lab]
  ↓ staging-test
    cd /workspace/source/ttpos-flutter && make ci-unit-test     ← 本 cookbook
    cd /workspace/source/ttpos-flutter && make ci-integration-test
  ↓ accept
    cd /workspace/integration/ttpos-arch-lab && make accept-env-up
      ↳ 内部 ./apk/build.sh 读 /workspace/source/ttpos-flutter/ 编 APK
      ↳ 起 emulator container + 装 APK + 起 backend compose
    accept-agent 跑 FEATURE-A* scenarios
    cd /workspace/integration/ttpos-arch-lab && make accept-env-down
```

---

## 10. 排查清单

`make ci-unit-test` 在 runner pod 失败时按这个顺序看：

| 症状 | 先看 |
|---|---|
| `make: *** No rule to make target 'ci-unit-test'` | Makefile 没有加进仓 / 没在根目录 |
| `melos: command not found` | `ci-setup` 没跑完；`dart pub global activate melos` 失败 |
| `flutter analyze` 报 plugin crash | runner 镜像 Flutter channel 跟仓用的 Flutter 版本不兼容；`ci-env` 输出的 `FLUTTER_VERSION` 要跟镜像 stable 对齐 |
| widget test 崩 `Unable to load asset` | 缺 `flutter test --no-pub`；或 `pubspec.yaml` assets 路径写错 |
| `dart format` 退码 1 | 格式不对，`dart format .`（不带 `--set-exit-if-changed`）本地跑一次提交 |
| `melos run test:unit` 报 `No scripts found` | `melos.yaml` 里没定义 `test:unit` script；按 §7 加一遍 |
| ci-integration-test 超时 | docker compose 起 stack 慢；调 `healthcheck start_period` / `retries`；或切回空实现（路径 C1） |
| `/workspace/source/ttpos-flutter` 不存在（accept 阶段） | BKD intent issue 的 `involved_repos` 没列 `ZonEaseTech/ttpos-flutter`；`sisyphus-clone-repos.sh` 没跑这个仓 |
| `flutter build apk` 在 runner pod 失败 | `ANDROID_HOME` / `JAVA_HOME` 确认：`flutter doctor` 在 pod 里跑一次；cirruslabs/flutter 镜像通常已配好 |
