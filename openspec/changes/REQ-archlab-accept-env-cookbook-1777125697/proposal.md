# REQ-archlab-accept-env-cookbook-1777125697: docs(cookbook): ttpos-arch-lab accept-env-up/down (compose + emulator + APK + endpoint)

## 问题

`docs/integration-contracts.md` 把 integration repo 的 lab 起 / 拆契约定到了
**「目标名 + stdout JSON 形状 + env 变量列表」**，并给了两份 Makefile 模板：

- §4.2 helm-based（K3s）
- §4.2.2 docker-compose-based（纯后端 stack）

但 sisyphus 实际典型的 integration repo —— `phona/ttpos-arch-lab` —— 是一类
**「mobile App + 后端 stack」端到端 lab**，需要在 `accept-env-up` 一条命令里同时：

1. 起 backend `docker compose` stack（业务 API + DB + 第三方 mock）
2. 起 Android emulator container（headless，KVM 不可用时降软件渲染）
3. 编 APK（`flutter build apk --release` + `--dart-define=API_BASE_URL=...` 把 backend
   endpoint 编进 App）或拉 GHCR prebuilt
4. `adb install -r` 装 APK 到 emulator
5. emit 多键 endpoint JSON（`endpoint` HTTP + `adb` ADB 地址 + `apk_package`），
   accept-agent 同时跑 backend HTTP scenarios 跟 mobile UI scenarios

§4.2 / §4.2.2 任何一个都没覆盖这套组合：helm 那份不带 emulator、compose 那份不带
APK build / install / 多键 endpoint。整 lab 团队无样板可抄，每个新接入的 mobile lab
要自己摸一遍坑（KVM fallback / port 自分配 / boot_completed 检测 / stdout 严格分流）。

## 根因

历史上 sisyphus 的 accept stage 长期 `skip_accept: true`（`ttpos-arch-lab` 没真接），
所以 mobile lab 的样板需求没暴露在文档欠账里。直到 self-dogfood
（REQ-self-accept-stage-1777121797）把 accept 跑通后，`docs/integration-contracts.md`
§4.2.2 docker-compose 模板才补齐 —— 但那是给纯后端 lab 用的，mobile lab 这一类
跟它的差异（emulator + APK + 多键 endpoint）足够大，硬塞进 §4.2.2 一个表会让契约文档
失去「一份契约 + 多份样板」的清晰分层。

## 方案

**新开 cookbook 目录 `docs/cookbook/`，第一份是 ttpos-arch-lab 这类 mobile lab 的食谱。**
不改契约，不改 Python 代码 —— `integration-contracts.md` 仍然是契约权威，cookbook
只是契约下的实现样板。

### Step 1：新增 `docs/cookbook/ttpos-arch-lab-accept-env.md`

完整 cookbook，包含 9 节：

| § | 内容 |
|---|---|
| 0 | TL;DR — 5 步 recipe 概览 |
| 1 | repo 布局（`docker-compose.accept.yml` / `emulator/` / `apk/` 三件套） |
| 2 | backend compose 骨架（lab + postgres，host port 自分配，healthcheck 必备） |
| 3 | Android emulator container（cirruslabs api-34；KVM fallback 软件模拟；`boot-wait.sh`） |
| 4 | APK 构建（`flutter build apk` + `--dart-define`；GHCR prebuilt 兜底） |
| 5 | endpoint JSON 多键扩展契约（`endpoint` 必需；`adb` / `apk_package` 扩展） |
| 6 | 完整 Makefile 范本（5 步 recipe；stderr / stdout 严格分离） |
| 7 | accept-agent prompt 段如何用多键 endpoint |
| 8 | 排查清单 |
| 9 | 跟 §4.2 helm / §4.2.2 compose 模板的关系 |

### Step 2：从 `integration-contracts.md` §4.2.2 加 cross-link

§4.2.2 是纯后端 stack 模板，紧挨着插一段「想看完整 mobile App + 后端 lab 食谱看 cookbook」
的引用，让读者按需跳。

### Step 3：从 `README.md` 文档索引追一行 cookbook 入口

新接入的业务团队第一眼能找到 cookbook 目录。

### Step 4：从 `CLAUDE.md` 文档索引追一行 cookbook 入口

repo 进 Claude session 时也能看到这份资源。

### Step 5：验证

无业务行为变化，无单元测试新增。靠：

- `openspec validate openspec/changes/REQ-archlab-accept-env-cookbook-1777125697 --strict` 通过
- `check-scenario-refs.sh` 全 scenario ID 引用解析（task / spec 之间互通）
- `make ci-lint && make ci-unit-test && make ci-integration-test` 全过
  （docs-only diff，BASE_REV scope 后无 *.py 变更 → ci-lint 立 pass；pytest 套不动）
- 文件存在 + 关键 section 标题匹配 grep

## 取舍

- **为什么单开 `docs/cookbook/` 目录而不是塞 `integration-contracts.md` §4.2.3** —— mobile lab
  cookbook 长（~250 行 + 多份代码块），塞契约文档会让 §4 节臃肿；契约（target 名 / JSON
  形状 / env 列表）要短而精，样板（实现步骤 / 容错 / 排查）可以长。两者分层。
- **为什么 cookbook 直接给 Makefile 范本而不是抽象 step 描述** —— sisyphus 的契约消费者
  是「想接入的 lab 团队」，他们要的是能 `cp -r` 进自己仓的可跑代码，不是抽象指引。
- **为什么 KVM fallback 默认走软件模拟而不是要求 host `/dev/kvm`** —— vm-node04 K3s
  sisyphus-runners namespace 当前没暴露 `/dev/kvm` device，cookbook 不能要求 host
  能力。软件模拟 boot 慢但 accept stage 1800s timeout 内绰绰有余，**默认能跑**比
  「快但要 host 配合」更符合自包含 lab 的设计。
- **为什么 endpoint JSON 要多键而不是只 `endpoint`** —— mobile lab 的 endpoint 含两个
  正交资源（HTTP + ADB），强塞一个 URL 字段（如 `http://localhost:1234?adb=...`）
  反而复杂。`endpoint` 仍是契约必需键 + 优雅降级（accept-agent 不读 `adb` 时只跑
  HTTP 部分），新接入只需读 README 就懂。
- **为什么不写 sibling REQ-flutter-accept-env-template 的 Flutter 模拟 backend cookbook** ——
  那是另一种 lab 形态（pure Flutter widget tests + mock backend，不真起 emulator），
  scope 不同；cookbook 目录设计就是「一形态一文件」，留给那个 REQ 自己写。
- **为什么不强制 ttpos-arch-lab 团队按这份 cookbook 重构他们仓** —— cookbook 是
  *推荐样板*不是*契约*；他们当前仓如果 Makefile 已经满足 §2.3 + §3 契约，就没问题。
  cookbook 是给新接入团队 + ttpos-arch-lab 后续改造时的参考。
