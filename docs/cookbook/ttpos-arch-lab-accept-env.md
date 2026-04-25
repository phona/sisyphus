# Cookbook: ttpos-arch-lab `accept-env-up` / `accept-env-down`

> 给 **integration repo `phona/ttpos-arch-lab`**（以及任何形似的「mobile e2e lab」repo）
> 一份能直接抄的 `make accept-env-up` / `make accept-env-down` 食谱：起后端
> compose stack → 启 Android emulator（headless）→ 装 APK → emit endpoint JSON
> → 让 sisyphus accept-agent 跑 FEATURE-A* scenarios。
>
> 适用场景：被测系统是 **「mobile App + 后端 stack」**，accept-agent 要同时面向
> HTTP endpoint（验业务接口）和 emulator 上 App（验 UI 流程）。
>
> 契约权威是 [`docs/integration-contracts.md`](../integration-contracts.md) §2.3 / §3 / §5；
> 本 cookbook 只是一份**实现样板**。冲突以契约文档为准。

## 0. TL;DR — 这份 recipe 给你什么

`make accept-env-up` 执行 5 步，**stdout 只在最后吐一行 JSON**（其余写 stderr）：

1. 起 backend `docker compose up -d --wait`，等 healthcheck 转绿
2. 启 Android emulator container（headless，软件渲染兜底）+ `adb` 等 boot 完成
3. `flutter build apk --release` 出 APK（或拉 prebuilt）
4. `adb install -r` 装到 emulator
5. `printf '{"endpoint":"http://localhost:<port>","adb":"127.0.0.1:<adb-port>","namespace":"<ns>"}\n'`

`make accept-env-down` 反向：`adb kill-server` → emulator container down → `docker compose down -v`，
全部 best-effort（前缀 `-` 或 `|| true`）。

## 1. repo 布局

`ttpos-arch-lab` 是 sisyphus 契约里的 **integration repo**（不是 source repo），
runner pod 把它 clone 到 `/workspace/integration/ttpos-arch-lab/`。整个 lab 自包含：
backend stack + emulator wrapper + APK 来源声明 + Makefile，全在一个仓里。

```
ttpos-arch-lab/
├── Makefile                              ← accept-env-up / accept-env-down
├── docker-compose.accept.yml             ← backend stack（compose 项目名 = $SISYPHUS_NAMESPACE）
├── emulator/
│   ├── docker-compose.emulator.yml       ← Android emulator container（独立 compose）
│   └── boot-wait.sh                      ← 等 emulator boot_completed 的辅助脚本
├── apk/
│   ├── source.txt                        ← APK 来源声明（git URL 或 GHCR artifact tag）
│   └── build.sh                          ← 拉 source / 编 APK / 输出到 ./apk/dist/app.apk
└── README.md                             ← 指回这份 cookbook
```

不强制目录细节，但 **Makefile + compose file + APK build script 三件套缺一不可**。

## 2. backend compose stack：`docker-compose.accept.yml`

骨架如下。要点：

- 服务名留一个 `lab`（业务 backend 入口），exposing 8080 但**不写 host port** ——
  让 docker 自动分配，避免并发 REQ 撞港。
- 每个 service 必须有 `healthcheck`，否则 `docker compose up -d --wait` 不会真等就绪。
- 同 compose 项目里可放 postgres / redis / mock 第三方等所有 backend 依赖。

```yaml
services:
  lab:
    image: ghcr.io/phona/ttpos-server-go:${TTPOS_SERVER_TAG:-main}
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgres://ttpos:ttpos@postgres:5432/ttpos?sslmode=disable
    ports:
      - "8080"                  # docker 自分配 host port
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/healthz"]
      interval: 5s
      timeout: 2s
      retries: 12
      start_period: 5s

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ttpos
      POSTGRES_PASSWORD: ttpos
      POSTGRES_DB: ttpos
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ttpos -d ttpos"]
      interval: 2s
      timeout: 3s
      retries: 30
```

> **为什么不直接用 helm chart 部 K3s lab？**
> ttpos-arch-lab 历史上是 helm-based（见 `integration-contracts.md` §4.2）。但 emulator 在
> K3s pod 里跑 KVM 通常没 `/dev/kvm` device，软件渲染又对 helm `--wait` 健康探针不友好；
> 切到 docker compose + DinD 跑在 sisyphus-runner pod 里，**emulator container 跟
> backend 共享 DinD daemon + 局域虚拟网络**，accept-agent 通过 host port 访问就行。

## 3. Android emulator container：`emulator/docker-compose.emulator.yml`

公共 emulator 镜像首选 `budtmo/docker-android` 或 `ghcr.io/cirruslabs/android-images`
（runner 镜像基于 `cirruslabs/flutter:stable`，跟 cirruslabs 的 emulator 系列对齐）。

```yaml
services:
  emulator:
    image: ghcr.io/cirruslabs/android-images:api-34
    privileged: true                       # KVM / binder 需要
    devices:
      - /dev/kvm                           # 有 KVM 走硬加速；无 KVM 走 -no-accel
    environment:
      EMULATOR_ARGS: "-no-window -no-audio -no-snapshot -gpu swiftshader_indirect"
    ports:
      - "5555"                             # adb daemon（host port docker 自分配）
      - "5554"                             # emulator console（仅 debug 用）
    healthcheck:
      test: ["CMD-SHELL", "adb -s emulator-5554 shell getprop sys.boot_completed | grep -q 1"]
      interval: 5s
      timeout: 3s
      retries: 60                          # boot 慢，给足重试
      start_period: 30s
```

**KVM fallback**：vm-node04 K3s sisyphus-runners namespace 没暴露 `/dev/kvm` 时，
docker run 会报 `error gathering device information ... /dev/kvm: no such file`。
两条路：

1. **声明 `EMULATOR_ARGS: "-no-accel ..."`**：纯软件模拟，boot 时间从 ~30s 涨到 ~3min，
   配套把 healthcheck `start_period` 调到 180s 即可。
2. **加 device plugin / privileged passthrough**：让 sisyphus-runner pod 拿到 host
   `/dev/kvm`。需要 host 上 `lsmod | grep kvm` 真有模块；vm-node04 是 KVM hypervisor 时
   原生支持，否则不可得。

> 软件模拟在 sisyphus accept 阶段是可接受的（accept stage timeout 1800s，缓 3min boot 没事）。
> 优先走软件路径，不依赖宿主能力 = 整个 lab repo 在任何 K3s/runner 组合都能跑。

### 3.1 `emulator/boot-wait.sh`

把 healthcheck 写两遍（compose 一遍 + bash 一遍）的目的：compose `--wait` 会等
healthcheck 第一次绿，但绿之后 `package manager`/`activity manager` 还可能没起完，
直接 `adb install` 会偶发失败。这个脚本把 install 前置稳定性补全。

```bash
#!/usr/bin/env bash
set -euo pipefail

ADB_HOST_PORT="${1:-5555}"
ADB_TARGET="127.0.0.1:${ADB_HOST_PORT}"

# 1. 让 host adb client 跟 emulator container 里的 adb daemon 握手
for i in {1..30}; do
  if adb connect "$ADB_TARGET" 2>/dev/null | grep -q "connected\|already"; then
    break
  fi
  sleep 2
done

# 2. 等 sys.boot_completed=1
adb -s "$ADB_TARGET" wait-for-device
for i in {1..60}; do
  if [ "$(adb -s "$ADB_TARGET" shell getprop sys.boot_completed | tr -d '\r')" = "1" ]; then
    break
  fi
  sleep 3
done

# 3. 再额外等 package manager 就绪（pm list packages 可调用）
for i in {1..30}; do
  if adb -s "$ADB_TARGET" shell pm list packages -f >/dev/null 2>&1; then
    echo "[boot-wait] emulator $ADB_TARGET ready" >&2
    exit 0
  fi
  sleep 2
done

echo "[boot-wait] emulator $ADB_TARGET never reached pm-ready state" >&2
exit 1
```

## 4. APK 构建：`apk/build.sh`

APK 来源有 3 种，cookbook 给最常见的 **「同 REQ 改 source repo + 当场编 APK」**：

```bash
#!/usr/bin/env bash
# apk/build.sh — 编 APK 写到 ./apk/dist/app.apk
#
# 依赖：runner 镜像 cirruslabs/flutter:stable 已带 Flutter SDK + Android SDK + Java。
set -euo pipefail

DIST="$(cd "$(dirname "$0")" && pwd)/dist"
mkdir -p "$DIST"

# source repo 在 sisyphus 约定路径，单仓 REQ 当 ttpos-flutter-app 改了：
SRC_REPO="${TTPOS_FLUTTER_REPO:-/workspace/source/ttpos-flutter-app}"

if [[ ! -d "$SRC_REPO" ]]; then
  echo "[apk/build] $SRC_REPO not found; falling back to GHCR prebuilt" >&2
  # 兜底：从 GHCR 拉一个固定 tag 的 APK artifact（业务自己维护）
  curl -fsSL \
    -H "Authorization: Bearer $SISYPHUS_GHCR_TOKEN" \
    "https://ghcr.io/v2/phona/ttpos-flutter-app/blobs/${TTPOS_APK_DIGEST:?missing}" \
    -o "$DIST/app.apk"
  exit 0
fi

cd "$SRC_REPO"
flutter pub get
flutter build apk --release \
  --dart-define=API_BASE_URL="${TTPOS_API_BASE_URL:?must point to compose lab}"

cp build/app/outputs/flutter-apk/app-release.apk "$DIST/app.apk"
echo "[apk/build] APK -> $DIST/app.apk" >&2
```

要点：

- **`--dart-define=API_BASE_URL`** 把 backend endpoint 编进 APK，让 emulator 内的 App
  指向 compose stack。Makefile 在调 `build.sh` 前会把 `TTPOS_API_BASE_URL` 算好。
- **prebuilt 兜底**：源仓没 clone 进 `/workspace/source/`（比如本 REQ 只改 backend、没改 App）
  时，从 GHCR 拉固定 digest 的 APK 跑回归。digest 由 `apk/source.txt` 声明，业务团队维护。

## 5. endpoint JSON 契约（多键扩展）

`integration-contracts.md` §3 规定 stdout 末行 JSON **必须**有 `endpoint` 字段。
mobile lab 在此基础上**再加两个非必需键**给 accept-agent 用：

```json
{
  "endpoint": "http://localhost:34567",
  "adb": "127.0.0.1:34568",
  "apk_package": "com.phona.ttpos",
  "namespace": "accept-req-xxx"
}
```

| key | 必需 | 给谁用 |
|---|---|---|
| `endpoint` | ✅ | accept-agent 跑后端 HTTP scenarios（hit `lab` 服务） |
| `adb` | （扩展） | accept-agent 跑 UI scenarios，先 `adb connect <addr>` 再 `adb shell input tap ...` 或 `am start` |
| `apk_package` | （扩展） | accept-agent 启 App 用 `am start -n <package>/<activity>` |
| `namespace` | （可选） | 跟 §3 一致，sisyphus 已通过 env 传一份；写一遍方便 debug |

> **额外键由 accept-agent 自己读** —— sisyphus orchestrator 只解析 `endpoint`
> 字段（`actions/create_accept.py` 里）。其他键透传给 accept-agent prompt context。
> accept-agent prompt 模板 (`prompts/accept.md.j2`) 里能直接用 `ctx.lab.adb` /
> `ctx.lab.apk_package`。

## 6. 完整 Makefile 范本

```makefile
.PHONY: accept-env-up accept-env-down

# sisyphus 注入；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default
COMPOSE_PROJECT_NAME := $(SISYPHUS_NAMESPACE)
BACKEND_COMPOSE_FILE  ?= docker-compose.accept.yml
EMULATOR_COMPOSE_FILE ?= emulator/docker-compose.emulator.yml
APK_PACKAGE ?= com.phona.ttpos

accept-env-up:
	@# 1. 起 backend stack
	@echo "[arch-lab] up: backend stack ($(COMPOSE_PROJECT_NAME))" >&2
	docker compose -p $(COMPOSE_PROJECT_NAME) -f $(BACKEND_COMPOSE_FILE) \
	    up -d --wait --wait-timeout 180 >&2
	@# 2. 算 backend host port（accept-agent 用 host 视角访问）
	@backend_port=$$(docker compose -p $(COMPOSE_PROJECT_NAME) \
	    -f $(BACKEND_COMPOSE_FILE) port lab 8080 | awk -F: 'END{print $$NF}'); \
	if [ -z "$$backend_port" ]; then \
	    echo "[arch-lab] FAIL: cannot resolve backend lab:8080 host port" >&2; \
	    exit 2; \
	fi; \
	echo "$$backend_port" > .accept-backend-port
	@# 3. 起 emulator container
	@echo "[arch-lab] up: emulator" >&2
	docker compose -p $(COMPOSE_PROJECT_NAME)-emu -f $(EMULATOR_COMPOSE_FILE) \
	    up -d --wait --wait-timeout 600 >&2
	@adb_port=$$(docker compose -p $(COMPOSE_PROJECT_NAME)-emu \
	    -f $(EMULATOR_COMPOSE_FILE) port emulator 5555 | awk -F: 'END{print $$NF}'); \
	if [ -z "$$adb_port" ]; then \
	    echo "[arch-lab] FAIL: cannot resolve emulator 5555 host port" >&2; \
	    exit 3; \
	fi; \
	echo "$$adb_port" > .accept-adb-port
	@# 4. boot-wait + 编 APK + 装 APK
	@adb_port=$$(cat .accept-adb-port); \
	./emulator/boot-wait.sh "$$adb_port" >&2
	@backend_port=$$(cat .accept-backend-port); \
	TTPOS_API_BASE_URL="http://localhost:$$backend_port" ./apk/build.sh >&2
	@adb_port=$$(cat .accept-adb-port); \
	adb connect "127.0.0.1:$$adb_port" >&2; \
	adb -s "127.0.0.1:$$adb_port" install -r ./apk/dist/app.apk >&2
	@# 5. emit endpoint JSON（**stdout 末行**，前面所有日志写 stderr）
	@backend_port=$$(cat .accept-backend-port); \
	adb_port=$$(cat .accept-adb-port); \
	printf '{"endpoint":"http://localhost:%s","adb":"127.0.0.1:%s","apk_package":"%s","namespace":"%s"}\n' \
	    "$$backend_port" "$$adb_port" "$(APK_PACKAGE)" "$(SISYPHUS_NAMESPACE)"

accept-env-down:
	-@# best-effort：每一步独立失败不阻塞下一步
	-adb kill-server 2>/dev/null || true
	-docker compose -p $(COMPOSE_PROJECT_NAME)-emu -f $(EMULATOR_COMPOSE_FILE) \
	    down --volumes --remove-orphans 2>&1 || true
	-docker compose -p $(COMPOSE_PROJECT_NAME) -f $(BACKEND_COMPOSE_FILE) \
	    down --volumes --remove-orphans 2>&1 || true
	-rm -f .accept-backend-port .accept-adb-port 2>/dev/null || true
```

要点：

- **stdout / stderr 严格分离**：`integration-contracts.md` §3 规定
  `result.stdout.splitlines()` 反向取第一个非空行，前面任何日志混进 stdout 都会
  让 sisyphus 错把 log 当 endpoint 解析。recipe 里 `>&2` / `@echo ... >&2` 全在 stderr。
- **两个 compose 项目**：backend 用 `$(COMPOSE_PROJECT_NAME)`，emulator 用
  `$(COMPOSE_PROJECT_NAME)-emu`，方便分开起 / 拆 / 排查；同 namespace 隔离 + 不互相干扰。
- **port 落盘** `.accept-backend-port` / `.accept-adb-port`：env-up 多步要用，写文件
  比 `$(shell ...)` 稳（make 函数在 recipe 间不能传值）；env-down 删掉。
- **`accept-env-down` 全 `-` 前缀**：partial up 失败时（emulator 没起来，backend 起来了）
  也要把 backend 拆掉，不能因为 adb / emulator 步骤报错就放弃 backend down。

## 7. 跟 sisyphus accept-agent 的对接

accept-agent prompt (`orchestrator/src/orchestrator/prompts/accept.md.j2`) 已经会
读 `ctx.lab.endpoint`。多键扩展（`adb` / `apk_package`）要 accept-agent 自己用。
推荐 prompt 段写：

```text
本 REQ 是 mobile App + 后端 stack 联合 e2e。lab 暴露：

- HTTP endpoint：{{ ctx.lab.endpoint }}（hit 后端业务 API）
- ADB：{{ ctx.lab.adb }}（emulator 已起 + APK {{ ctx.lab.apk_package }} 已装）

跑 UI scenario 时：
  adb connect {{ ctx.lab.adb }}
  adb -s {{ ctx.lab.adb }} shell am start -n {{ ctx.lab.apk_package }}/.MainActivity
  adb -s {{ ctx.lab.adb }} shell input tap <x> <y>
  adb -s {{ ctx.lab.adb }} shell screencap -p > /tmp/screen.png

跑 backend scenario 时：
  curl -fsS {{ ctx.lab.endpoint }}/api/...
```

如果业务 prompt 没改 / `ctx.lab` 只读 `endpoint`：accept-agent 就只能跑 HTTP 部分，
mobile UI scenarios 跳过 —— 这是**优雅降级**：endpoint 字段是契约，缺它 fail；
adb 字段是扩展，缺它降级。

## 8. 排查清单

`make accept-env-up` 失败时按这个顺序看：

| 症状 | 先看 |
|---|---|
| backend 起不来 | `docker compose -p $NS -f docker-compose.accept.yml logs --tail=80 lab` |
| emulator 永远 boot 不完 | `docker compose -p $NS-emu logs emulator | grep -i "boot_completed\|kvm\|swiftshader"`；多半是 KVM 缺 + start_period 不够 |
| `adb connect` 连不上 | `docker compose -p $NS-emu port emulator 5555` 看 host port 真分配没；`adb devices` 看有没有 `offline` |
| APK 编译失败 | `apk/build.sh` 末尾的 stderr；`flutter doctor` 在 runner pod 里直接跑一次 |
| `adb install` 报 `INSTALL_FAILED_INVALID_APK` | APK abi 跟 emulator 不一致，build.sh 加 `--target-platform android-x64` |
| stdout 末行不是 JSON | recipe 哪一步把 log 输出到了 stdout（漏了 `>&2`）；`make accept-env-up 2>/dev/null \| tail -1` 看 |
| sisyphus 解析不到 `endpoint` | endpoint URL 必须是 **runner pod 视角** reachable —— `localhost:<port>` 在 DinD 里走 host network 通；不要写 `http://lab:8080`（compose 内部 DNS 在 runner 视角下不通） |

## 9. 跟现有契约 / 历史模板的关系

- `docs/integration-contracts.md` §4.2 helm-based 模板：当 lab 部 K3s 时用，跟本 cookbook **互补不替代**。
- `docs/integration-contracts.md` §4.2.2 docker-compose 通用模板：纯后端 stack 的简化版；mobile lab 在它上面加 emulator / APK，就是本 cookbook。
- `docs/cookbook/` 后续可能再加 `flutter-mock-backend.md`（`REQ-flutter-accept-env-template-1777125538`）等：每份 cookbook 限定一种 lab 形态，互不重叠。
