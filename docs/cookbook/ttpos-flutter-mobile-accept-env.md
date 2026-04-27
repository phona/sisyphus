# Cookbook: ttpos-flutter mobile `accept-env-up` / `accept-env-down`（Flutter 自承 integration repo）

> 给 **Flutter 源仓**（如 `ZonEaseTech/ttpos-flutter`）一份能直接抄的
> `accept-env-up` / `accept-env-down` 食谱：在 **不依赖 `ttpos-arch-lab` / 不起
> Android emulator** 的前提下，让 Flutter 仓自己充当 mobile accept 的 integration
> repo —— 起 mock 后端 stack → 发布固定 endpoint JSON → accept-agent 用 Dart
> integration test 或纯 HTTP 黑盒验证。
>
> 适用场景：业务只想验「**App ↔ 后端 HTTP 协议层**」，不想拉 emulator + APK 安装那条
> 重链路；或者团队还没有 `ttpos-arch-lab` 同款 K3s lab，需要一个轻量过渡方案。
>
> 契约权威是 [`docs/integration-contracts.md`](../integration-contracts.md) §2.3 / §3 / §4.2；
> 完整的「emulator + APK + 多键 endpoint JSON」食谱见
> [`docs/cookbook/ttpos-arch-lab-accept-env.md`](ttpos-arch-lab-accept-env.md)；
> Flutter 源仓侧 `ci-*` Makefile 见
> [`docs/cookbook/ttpos-flutter-makefile.md`](ttpos-flutter-makefile.md)。
> 本 cookbook 只覆盖 Flutter 仓**自承 integration repo 角色**的场景，冲突以契约文档为准。

---

## 0. TL;DR — 你需要知道的 3 件事

1. **Flutter 仓默认是 source repo，不实现 `accept-env-up` / `accept-env-down`**。
   只有你**主动**让它兼任 integration repo（团队还没有 arch-lab，或不需要 emulator
   级 e2e）才往根 Makefile 加这两个 target。决策树见 §1。
2. **endpoint JSON 契约不变**：`make accept-env-up` 退码 0 + **stdout 末行**写
   `{"endpoint":"…","namespace":"…"}`。Flutter 仓自承 integration repo 时这条不变 ——
   accept-agent 读到 `endpoint` 就能跑 HTTP scenarios。
3. **mobile 限定**：UI / emulator / `flutter drive` 的 e2e 仍由 `ttpos-arch-lab` 做。
   本 cookbook 给的 recipe 只验 HTTP 层（mock backend + Dart `package:test` 或
   `flutter test --no-pub` 模式），**不起 emulator**。要起 emulator 请直接抄
   `ttpos-arch-lab-accept-env.md`，**不要**把 emulator 进 Flutter 仓。

---

## 1. 什么时候需要这份 cookbook（决策树）

```
本 REQ 要在 accept 阶段验什么？
├── App UI flow / 真机交互 / emulator                → 用 ttpos-arch-lab cookbook（不本 cookbook）
├── 只验后端 HTTP 协议（lab endpoint）              → 选项 A：用 arch-lab backend chart，不起 emulator
│                                                       选项 B：用本 cookbook（Flutter 自承 integration repo）
└── 既不需要后端 lab，也不验 UI                     → 不需要 accept-env，REQ 只做 staging-test 即可
```

什么时候选**本 cookbook**而不是用 arch-lab：

| 情况 | 建议 |
|---|---|
| 团队已经有 `ttpos-arch-lab` | 优先用 arch-lab cookbook —— integration repo 集中管，避免 Flutter 仓背 lab 复杂度 |
| 团队**还没**有 arch-lab（或临时过渡） | 用本 cookbook 起轻量 docker-compose / helm 后端 stack |
| 验证目标只是 HTTP 协议（无 UI） | 用本 cookbook 更轻 —— 不需要 emulator helm chart / boot-wait |
| 验证目标有真机 UI flow | **不要**用本 cookbook —— 把 emulator 塞进 Flutter 仓会污染 source repo 角色 |

> 下面整篇 cookbook 假设你已经决定 Flutter 仓兼任 integration repo。如果是
> "只用 arch-lab"，回去看 `ttpos-arch-lab-accept-env.md`。

---

## 2. repo 布局（自承 integration repo 后）

```
ttpos-flutter/
├── Makefile                              ← ← ← 加 accept-env-up / accept-env-down
├── pubspec.yaml                          ← melos workspace 声明
├── melos.yaml
├── apps/                                 ← 业务 packages（不动）
├── packages/                             ← 共用 lib（不动）
├── tests/
│   ├── docker-compose.accept.yml         ← 本 cookbook 新增：mock backend stack
│   └── accept/
│       ├── Dockerfile.backend-stub       ← （可选）自己 build 的 mock 服务镜像
│       └── seed/                         ← seed SQL / fixtures
└── ...
```

**关键约束**：

- `tests/docker-compose.accept.yml` **跟 ci-integration-test 用的 compose 文件分开**
  （后者通常在 `tests/docker-compose.integration.yml`）。accept 阶段的 stack 生命周期
  比 integration-test 长（accept-agent 要跑多个 scenarios），合并会让两边互相影响。
- Flutter 业务代码（`apps/` / `packages/`）**不要**为 accept 阶段加任何代码 ——
  accept-agent 跑的是 HTTP 黑盒，不读 Dart 源码。

---

## 3. mock backend stack：`tests/docker-compose.accept.yml`

最小骨架（mock backend + postgres + redis）：

```yaml
# tests/docker-compose.accept.yml
services:
  backend:
    image: ghcr.io/zoneasetech/ttpos-server-go:${TTPOS_BACKEND_TAG:-main}
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgres://ttpos:ttpos@postgres:5432/ttpos?sslmode=disable
      REDIS_URL: redis://redis:6379/0
      LOG_LEVEL: info
    ports:
      - "8080"                           # 让 compose 自分配 host 随机端口（避免并发撞车）
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8080/healthz"]
      interval: 3s
      timeout: 2s
      retries: 20
      start_period: 5s

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ttpos
      POSTGRES_PASSWORD=***FILTERED***
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ttpos"]
      interval: 2s
      timeout: 1s
      retries: 30

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 1s
      retries: 30
```

要点：

- **`backend` 是 service 名硬约定**，Makefile 里 `docker compose port backend 8080`
  按这个名取宿主端口。改名记得同步改 Makefile。
- **`ports: ["8080"]`（无映射）让 compose 自分配 host 端口**，配合 `-p
  $SISYPHUS_NAMESPACE` 实现**并发跑多个 REQ 不撞车**。同 host 不同 namespace
  端口隔离由 docker daemon 负责。
- **`healthcheck` 必填**：`docker compose up --wait` 只等到所有 service `healthy`
  才返回。没声明 healthcheck 时 `--wait` 仅等容器 running，跟"backend 真就绪"差一截，
  accept-agent 在那条窗口期内打 endpoint 会偶发 `connection refused`。
- **不要在 compose 里固定 8080:8080**：runner 上同时跑多个 REQ 时 host port 80
  80 会撞车（参考 integration-contracts §4.2.2）。

---

## 4. Makefile：accept-env-up / accept-env-down

直接续在 [`docs/cookbook/ttpos-flutter-makefile.md`](ttpos-flutter-makefile.md) §6 给的 `ci-*` Makefile 后面：

```makefile
# ── accept-env-up / accept-env-down（仅 Flutter 仓自承 integration repo 时启用）─────
# sisyphus 注入；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default
COMPOSE_PROJECT_NAME := $(SISYPHUS_NAMESPACE)
ACCEPT_COMPOSE ?= tests/docker-compose.accept.yml

.PHONY: accept-env-up accept-env-down

# accept-env-up
#   1. 起 mock backend stack（compose --wait 等 healthcheck）
#   2. 用 docker compose port 取宿主分配的 host 端口
#   3. stdout 末行吐 endpoint JSON（契约见 integration-contracts §3）
accept-env-up:
	@echo "[ttpos-flutter-lab] up: $(COMPOSE_PROJECT_NAME)" >&2
	docker compose -p $(COMPOSE_PROJECT_NAME) -f $(ACCEPT_COMPOSE) \
	    up -d --wait >&2
	@port=$$(docker compose -p $(COMPOSE_PROJECT_NAME) -f $(ACCEPT_COMPOSE) \
	    port backend 8080 | awk -F: 'END{print $$NF}'); \
	if [ -z "$$port" ]; then \
	    echo "[ttpos-flutter-lab] FAIL: cannot resolve backend host port for service 'backend' :8080 (project=$(COMPOSE_PROJECT_NAME))" >&2; \
	    exit 1; \
	fi; \
	printf '{"endpoint":"http://localhost:%s","namespace":"%s","stack":"flutter-self-hosted"}\n' \
	    "$$port" "$(SISYPHUS_NAMESPACE)"

# accept-env-down
#   幂等；失败 best-effort（参照 integration-contracts §2.3 "幂等性硬要求"）
accept-env-down:
	-docker compose -p $(COMPOSE_PROJECT_NAME) -f $(ACCEPT_COMPOSE) \
	    down --volumes --remove-orphans 2>&1 || true
```

要点：

- **stdout 只末行吐 JSON**，所有进度日志写 stderr（`>&2`）。`docker compose up --wait`
  默认日志走 stdout，必须 `>&2` 重定向，否则 sisyphus 解析末行 JSON 会撞上 compose
  output。
- **末行 `\n` 必填**：sisyphus 解析 `result.stdout.splitlines()` 反向第一个非空行，
  没换行符外层 shell 会吞最后一行。
- **`stack` 字段是扩展键**（`endpoint` 才是契约必需）。值 `"flutter-self-hosted"`
  让 accept-agent 在 prompt 里能区分 lab 来源（vs `"arch-lab"` / `"docker-compose"`），
  写 scenario 时可以条件化（"如果 stack == flutter-self-hosted 跳过 emulator-only
  scenarios"）。
- **`accept-env-down` 用 `-` 前缀 + `|| true`**：down 失败不阻塞状态机
  （integration-contracts §2.3 best-effort 语义）。`--volumes` 清掉 named volumes
  避免下次 `accept-env-up` 拿到 stale 数据库。

---

## 5. accept-agent 怎么用这个 endpoint

accept-agent 读 stdout 末行 JSON 的 `endpoint` 字段，用于直接打后端 HTTP API。
本 cookbook 的 lab **不起 emulator**，所以 scenario 写法跟「全后端 stack」一致：

```dart
// （示例）accept-agent 跑的 Dart integration test 片段
final endpoint = Platform.environment['TTPOS_API_BASE_URL']!;  // sisyphus 注入
final r = await http.get(Uri.parse('$endpoint/api/v1/store/list'));
expect(r.statusCode, 200);
```

**不能用本 cookbook 验**的 scenario 类型：

| scenario 类型 | 验法 | 为什么不在本 cookbook |
|---|---|---|
| App 启动后能登录 | flutter drive + emulator | 没 emulator |
| 收银员扫码识别条码 | UI flow + camera plugin mock | 没 emulator |
| 跨页面状态保留 | widget integration_test | 没 emulator |
| 后端拒绝错误 token 401 | HTTP request | ✅ 本 cookbook 适合 |
| 创建订单流程数据正确 | HTTP API 链 | ✅ 本 cookbook 适合 |

凡是表里**没勾**的 scenario，强烈建议改用 `ttpos-arch-lab-accept-env.md` 路径。

---

## 6. SISYPHUS_NAMESPACE 与并发隔离

sisyphus 在调 `make accept-env-up` 前注入 `SISYPHUS_NAMESPACE`（accept-stage 一 REQ
一 namespace，格式形如 `accept-req-flutter-xxx-yyy`）。Makefile 把它喂给 `docker
compose -p $(COMPOSE_PROJECT_NAME)`：

- compose project name 决定 docker network / container name 前缀 → 不同 REQ 不撞
- 但 **host 端口仍是 host 资源**，所以 `ports: ["8080"]`（无映射）让 docker
  自分配，再用 `docker compose port` 反查 → 真正的并发安全靠这套组合，不是单 `-p`

`accept-env-down` 用同一个 `-p $COMPOSE_PROJECT_NAME` 才能精准 down 自己起的 stack；
忘传 `-p` 会 down 默认 project（基于 cwd 名），很可能误清别 REQ 的 stack。

---

## 7. 跟其它 cookbook 的关系

| | 本 cookbook | `ttpos-flutter-makefile.md` | `ttpos-arch-lab-accept-env.md` |
|---|---|---|---|
| repo 角色 | Flutter 仓**自承** integration repo | Flutter 仓作 source repo（默认） | `ttpos-arch-lab` 作 integration repo |
| Makefile target | `accept-env-up` / `accept-env-down` | `ci-lint` / `ci-unit-test` / `ci-integration-test` | `accept-env-up` / `accept-env-down` |
| sisyphus 调用阶段 | accept | staging-test / dev-cross-check | accept |
| emulator | ❌（无 UI 验证） | ❌（不需要） | ✅（API 34 headless） |
| APK 编译 | ❌ | （可选 ci-build） | ✅（apk/build.sh） |
| 后端 stack | docker-compose mock | ❌ | helm chart |
| 推荐度 | 过渡 / 团队没 arch-lab | ✅ 必备 | ✅ 完整 mobile e2e 时首选 |

**典型 REQ 流程（自承场景）**：

```
REQ involved_repos: [ZonEaseTech/ttpos-flutter]    ← 单仓，Flutter 自承 integration
  ↓ staging-test
    cd /workspace/source/ttpos-flutter && make ci-unit-test       ← ttpos-flutter-makefile cookbook
    cd /workspace/source/ttpos-flutter && make ci-integration-test
  ↓ accept（自承 integration repo 关键差异）
    cd /workspace/source/ttpos-flutter && make accept-env-up      ← 本 cookbook
      ↳ docker-compose 起 mock backend
      ↳ stdout 末行吐 endpoint JSON
    accept-agent 跑 HTTP scenarios（无 UI）
    cd /workspace/source/ttpos-flutter && make accept-env-down    ← 本 cookbook
```

**典型 REQ 流程（arch-lab 场景，对比）**：

```
REQ involved_repos: [ZonEaseTech/ttpos-flutter, ZonEaseTech/ttpos-arch-lab]
  ↓ staging-test
    cd /workspace/source/ttpos-flutter && make ci-unit-test       ← ttpos-flutter-makefile cookbook
  ↓ accept
    cd /workspace/integration/ttpos-arch-lab && make accept-env-up  ← arch-lab cookbook
      ↳ helm 起 backend + emulator + 编 APK + 装 APK
    accept-agent 跑 HTTP + UI scenarios
    cd /workspace/integration/ttpos-arch-lab && make accept-env-down
```

---

## 8. 排查清单

`make accept-env-up` 在 runner pod 失败时按这个顺序看：

| 症状 | 先看 |
|---|---|
| `docker: command not found` | runner 镜像缺 docker；在 sisyphus runner image 是 docker DinD，确认 daemon 起来：`docker info` |
| `docker compose up --wait` 卡 5 分钟超时 | service 没有 healthcheck；或 healthcheck 路径错（`/healthz` vs `/health`） |
| stdout 末行不是 JSON 而是 compose log | 没把 compose 日志重定向到 stderr；检查 Makefile 是否有 `>&2` |
| accept-agent 报 `connection refused` | 端口取错；`docker compose port backend 8080` 输出格式是 `0.0.0.0:32768`，要 `awk -F: 'END{print $$NF}'` 取最后一段 |
| 跑两次 REQ 第二次端口撞 | host port 在 compose 文件里写死了；改成 `ports: ["8080"]`（只暴露内部端口） |
| `accept-env-down` 报 stack 不存在 | down 用了不同 project name；确保 up / down 用同一个 `-p $(COMPOSE_PROJECT_NAME)` |
| stale 数据库（上次 REQ 的数据残留） | down 没带 `--volumes`；改成 `down --volumes --remove-orphans` |
| accept 阶段 sisyphus 发现不了 endpoint | endpoint JSON 没换行；`printf` 末尾必须 `\n` |
| backend healthcheck `wget: command not found` | server-go alpine 镜像可能没装 wget；改成 `["CMD", "curl", "-fsS", "http://localhost:8080/healthz"]`，或镜像里加 wget |

如果排查全过仍失败，最好的兜底是切回 arch-lab 路径
（[`ttpos-arch-lab-accept-env.md`](ttpos-arch-lab-accept-env.md)） —— 那条路径有
更成熟的 healthcheck / boot-wait 处理。

---

## 9. 不要做的事

> 想做 UI / flutter drive 验证？走 [`ttpos-arch-lab-accept-env.md`](./ttpos-arch-lab-accept-env.md) —— Flutter source repo 这层只跑 dart unit / widget test，不背 emulator。

- ❌ **不要在 Flutter 仓里塞 Android emulator** —— emulator 容器要 `privileged:
  true` + 资源大，污染 source repo；UI 验证统一走 arch-lab。
- ❌ **不要让 `accept-env-up` 等价于 `ci-integration-test`** —— integration-test
  生命周期短（一次跑完即拆），accept-env 是常驻 stack 给 accept-agent 跑多 scenario。
  两个 compose 文件分开。
- ❌ **不要忽略 `SISYPHUS_NAMESPACE`** —— 一定喂给 `docker compose -p`，否则并发跑
  多 REQ 互相串扰。
- ❌ **不要在 stdout 输出非契约日志** —— accept-env-up stdout 只能末行有 JSON。
  其它日志全写 stderr。
- ❌ **不要把这套 Makefile target 写进 ttpos-arch-lab** —— arch-lab 已经是 integration
  repo，自己一份 helm-based recipe。两套 lab 不要并存。
