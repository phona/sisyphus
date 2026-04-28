# Design: ttpos-arch-lab accept-env cookbook

## 设计目标

给「mobile App + 后端 stack」型 integration repo 一份能直接抄的
`accept-env-up` / `accept-env-down` 实现样板，**不动**
`docs/integration-contracts.md` 契约本身。

## 关键决策

### D1：cookbook 单开目录 `docs/cookbook/`，不塞契约文档

| 选项 | 优 | 缺 |
|---|---|---|
| A. 塞 `integration-contracts.md` §4.2.3 | 单文件，导航简单 | §4 一节膨胀；契约（短而精）跟样板（长而具体）混层 |
| B. **新开 `docs/cookbook/` 目录** ✅ | 契约 / 样板分层；后续多种 lab 形态各自一文件 | 多一个目录 + 跨链 |

选 B：将来 `flutter-mock-backend.md`（REQ-flutter-accept-env-template）/ `go-pure-http.md`
等可以共享同一目录，契约文档保持轻。

### D2：KVM 默认走软件模拟兜底

| 选项 | 优 | 缺 |
|---|---|---|
| A. 强制宿主暴露 `/dev/kvm` | emulator boot ~30s | vm-node04 K3s sisyphus-runners ns 没配；新接入要先动基础设施 |
| B. **默认软件模拟，KVM 可选优化** ✅ | 任何 K3s pod 都能跑 | boot ~3min，需要把 healthcheck `start_period` 调大 |

选 B：cookbook 应「**自包含**」—— 拿到样板 + runner 镜像就能跑，不依赖 host 能力。
KVM 是「锦上添花」，文档里给如何加但默认禁用。

### D3：endpoint JSON 多键扩展，保留 `endpoint` 为单一契约必需键

`docs/integration-contracts.md` §3 规定 stdout 末行 JSON `endpoint` 字段必需。
mobile lab 多了一个 ADB 资源，三种处理方式：

| 选项 | 优 | 缺 |
|---|---|---|
| A. 把 ADB 塞进 `endpoint` URL 查询参数 (`?adb=...`) | 单键不破坏契约 | 解析复杂；`endpoint` 语义不再是 HTTP base |
| B. 改契约成 `endpoints: {http: ..., adb: ...}` 多键对象 | 表达力强 | 破坏 §3 + 所有现有 lab；要联动 `actions/create_accept.py` |
| C. **保留 `endpoint` HTTP base + 加扩展键 `adb` / `apk_package`** ✅ | 不动契约；老 lab 兼容；accept-agent 按需消费 | 文档要明确「扩展键非必需，缺它降级」 |

选 C：sisyphus orchestrator (`actions/create_accept.py`) 只解析 `endpoint`，多余字段
透传给 accept-agent prompt context，accept-agent 自己拍板用不用。

### D4：APK 来源声明用 `apk/source.txt` + `apk/build.sh` 双层

| 选项 | 优 | 缺 |
|---|---|---|
| A. 直接在 Makefile 里写 `flutter build apk ...` | 一目了然 | 来源切换（GHCR prebuilt vs 当场编）逻辑写 Makefile 太丑 |
| B. **`apk/source.txt` 声明 + `apk/build.sh` 抽象** ✅ | 来源策略可演化（git / GHCR / S3 等）；Makefile 干净 | 多两个文件 |

选 B：APK 来源是业务团队选型变量（开发期编译、稳定期 prebuilt 加速），抽到独立脚本
让 Makefile 只关心「调 build.sh + 装到 emulator」。

### D5：两个 compose 项目（backend / emulator）分开起

| 选项 | 优 | 缺 |
|---|---|---|
| A. 同一份 compose file 起 backend + emulator | 单 `up -d --wait` 同时等 | emulator boot 慢拖累 backend 探针 timeout；compose 不允许混不同优先级 healthcheck |
| B. **两个 compose 项目（`$NS` + `$NS-emu`）** ✅ | 分别 `--wait`，timeout 各自调；故障定位清晰 | Makefile 多两条 docker compose 调用 |

选 B：backend `--wait-timeout 180`，emulator `--wait-timeout 600`，分别匹配实际启动时间。

### D6：stdout / stderr 严格分流是 cookbook 必须强调的点

`docs/integration-contracts.md` §3 + §4.2.2 / §3 都提到 stdout 末行约定，但实际写
Makefile 时新手很容易把 `@echo "step done"` 漏成 stdout，让 sisyphus 把 log 当
endpoint 解析。cookbook 把这点提到第 6 节（完整 Makefile 范本）的「要点」首条 +
第 8 节排查清单第 6 条，提高可发现性。

## 风险

- **emulator 镜像更新 / API level 升级**：cookbook 钉死 `cirruslabs/android-images:api-34`，
  业务升级时要改这个 tag。可接受 —— cookbook 是样板不是固化产物。
- **DinD 内嵌套 docker compose 的资源压力**：backend stack + emulator container 跑在
  sisyphus-runner pod 的 DinD 里，pod 默认 8 GiB cgroup 上限可能在大 stack 下吃紧。
  排查清单第 1/2 条提了 `docker compose logs`，发现资源压力的入口够用；后续如果常态
  OOM，可考虑给 mobile lab REQ 加 pod 资源 hint。

## 不在本 REQ scope

- 不写 ttpos-arch-lab 实际仓代码（cookbook 是 sisyphus 仓的文档，ttpos-arch-lab 仓
  自己照样板改是另一个 REQ）
- 不改 sisyphus orchestrator / accept-agent prompt（多键 endpoint 是文档约定，
  accept-agent 在 prompt 段读 ctx.lab.adb 时业务团队自己更新 prompt）
- 不补 `dev_cross_check` / `staging_test` 对 cookbook 内容的 lint（无 verifiable 校验项）
