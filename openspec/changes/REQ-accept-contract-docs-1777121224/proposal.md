# REQ-accept-contract-docs-1777121224: docs(contracts): rename accept-up/down → accept-env-up/down + Docker Compose template

> **超 REQ 命名校正**（REQ-rename-accept-targets-1777124774）：本 change 最初
> 把 target 名定为 `ci-accept-env-up` / `ci-accept-env-down`，落到 docs / README /
> CLAUDE.md 全部位置。后续审视发现 `ci-` 前缀有误导性 —— accept env 不在
> PR-CI 热路径，跟 ttpos-ci `ci-*` 家族不同语义层。本 change 的最终落地名
> 已统一为 `accept-env-up` / `accept-env-down`（drop `ci-` 前缀）。本文档已
> 跟 `accept-env-target-naming` 能力 spec 同步到最终命名，避免 `openspec apply`
> 时引入过期断言。merged PR #87 的 commit 历史保留中间命名作为足迹。

## 问题

`docs/integration-contracts.md` 把 integration repo 的 lab 起 / 拆 target 立成
**`make accept-up`** / **`make accept-down`**。但 sisyphus orchestrator 实际调用的
是不同的版本：

- `orchestrator/src/orchestrator/actions/create_accept.py` 跑 `make accept-env-up`
- `orchestrator/src/orchestrator/actions/teardown_accept_env.py` 跑 `make accept-env-down`
- `orchestrator/src/orchestrator/prompts/accept.md.j2` 也提 `accept-env-up` / `accept-env-down`
- `orchestrator/docs/sisyphus-integration.md` 列的就是 `accept-env-up` / `down`

**契约文档跟代码错位** —— 业务 repo 团队照 `docs/integration-contracts.md` 实现
`accept-up:` target 后接入 sisyphus，sisyphus 跑 `accept-env-up`，
make 直接 `No rule to make target` 退非 0，env-up 永远失败。这是个静默的
"读了文档反而踩坑"陷阱。

同步问题暴露在 4 份文档：

| 文档 | 引用形式 | 影响 |
|---|---|---|
| `docs/integration-contracts.md` | §1 / §2.3 / §3 / §4.2 / §5 / §8 多处 | **canonical 契约**，业务 repo 接入第一手参照 |
| `docs/architecture.md` | mermaid 节点 + 角色分工表 + Stage 表 + env 表 + roadmap | 架构权威，列名失准 |
| `CLAUDE.md` | "Stage 流" 一行示意 | repo 进 Claude session 必读 |
| `README.md` | mermaid 流程图 + 接入新 repo 表 | 一进 repo 看到的第一份介绍 |

此外 `docs/integration-contracts.md` §4.2 只给了 **helm-based** integration repo 模板。
不所有团队都在 K3s 部 lab —— 跑 docker compose stack 的项目同样需要一个
能直接抄的 `accept-env-up:` / `accept-env-down:` 实现，stdout 尾行也得吐
endpoint JSON。补一个 **docker-compose 模板**让接入路径更广。

## 根因

target 命名 M-x 之间发生过迁移（`accept-up` → `accept-env-up`），代码侧改完没回头同步契约文档。审计没发现是因为：

1. sisyphus 自己的 dev_cross_check / staging_test 不读 integration repo 那部分文档
2. accept 阶段 sisyphus 内部还没真接生产 lab（`skip_accept: true` 默认）—— 长期没在
   生产路径暴露
3. `docs/architecture.md` §13 演进路线还残留 "接 ttpos-arch-lab 真 e2e —— accept-up /
   accept-down 落到生产 lab"，进一步固化错觉契约名是旧的

## 方案

**纯文档同步 + 模板补全** —— 不改任何 Python 代码 / Makefile / 测试，因为代码本来就对。

### Step 1：契约权威 `docs/integration-contracts.md`

- §1 介绍段：`accept-up / accept-down` → `accept-env-up / accept-env-down`
- §2.3 表格：target 列改名；保留 "谁调 / 在哪跑 / 期望"，调用路径 `cd /workspace/integration/<repo-name> && make accept-env-up`
- §3 标题 `accept-up 的 stdout JSON 契约` → `accept-env-up 的 stdout JSON 契约`
- §3 实现建议代码块 target 名 `accept-up:` → `accept-env-up:`
- §4.2 模板 `.PHONY: accept-up accept-down` → `.PHONY: accept-env-up accept-env-down`，
  recipe 头部同步
- §4.2 **新增** docker-compose 模板（§4.2.2）：用 `docker compose up -d` 起 stack，
  `docker compose port` 取宿主端口，stdout 尾行 endpoint JSON 形如 `http://localhost:<port>`
- §5 env 表 `SISYPHUS_STAGE` 行：`accept-up / accept-down` → `accept-env-up / accept-env-down`
- §8 排查清单第 4 条：`accept-up 失败` → `accept-env-up 失败`

### Step 2：架构权威 `docs/architecture.md`

- §2 mermaid 节点 `make accept-up` / `make accept-down` 改名
- §5 角色分工表 "机械 checker" 行 `accept-up/down` → `accept-env-up/down`
- §6 Stage 表 7a / 8 行：`make accept-up` / `make accept-down` 改名
- §7 数据流原语表 `make accept-up / accept-down` → `make accept-env-up / accept-env-down`
- §8 env 表 `SISYPHUS_STAGE` 行：`accept-up / accept-down` 改名
- §13 roadmap：`accept-up / accept-down 落到生产 lab` → `accept-env-up / down 落到生产 lab`

### Step 3：`CLAUDE.md`

- "Stage 流" 一行 `make accept-up + agent ... + make accept-down 必跑` → `make accept-env-up ... + make accept-env-down 必跑`

### Step 4：`README.md`

- §"当前架构" mermaid `make accept-up` / `make accept-down` 改名
- §"接入新业务 repo" 表 target 列两行 `make accept-up` / `make accept-down` 改名

### Step 5：补 docker-compose integration repo 模板（落 `integration-contracts.md` §4.2.2）

```makefile
.PHONY: accept-env-up accept-env-down

# integration repo 用 docker compose 起 lab 的最小模板。
# 业务自己挑 helm 或 compose，sisyphus 不在乎实现，只要 stdout 尾行有 endpoint JSON。
SISYPHUS_NAMESPACE ?= accept-default
COMPOSE_PROJECT_NAME := $(SISYPHUS_NAMESPACE)
COMPOSE_FILE ?= docker-compose.accept.yml

accept-env-up:
	docker compose -p $(COMPOSE_PROJECT_NAME) -f $(COMPOSE_FILE) up -d --wait
	@port=$$(docker compose -p $(COMPOSE_PROJECT_NAME) -f $(COMPOSE_FILE) port lab 8080 | cut -d: -f2); \
	printf '{"endpoint":"http://localhost:%s","namespace":"%s"}\n' "$$port" "$(SISYPHUS_NAMESPACE)"

accept-env-down:
	-docker compose -p $(COMPOSE_PROJECT_NAME) -f $(COMPOSE_FILE) down --volumes --remove-orphans || true
```

### Step 6：验证

无业务行为变化，无单元测试新增。靠：

- `openspec validate openspec/changes/REQ-accept-contract-docs-1777121224 --strict` 通过
- `grep -RIn 'make accept-up\|make accept-down\|accept-up:\|accept-down:'` 在 `docs/`、`README.md`、`CLAUDE.md`、`orchestrator/src/` 范围内零命中
- `grep -RIn 'accept-env-up\|accept-env-down'` 在 `docs/integration-contracts.md` ≥ 6 处命中（§2.3 / §3 / §4.2 / §4.2.2 / §5 / §8）
- `make ci-lint && make ci-unit-test && make ci-integration-test` 全过（self-dogfood，零行为变化只须不打破）

## 取舍

- **为什么不动 orchestrator 代码** —— 代码本来就对（`create_accept.py` / `teardown_accept_env.py` / `accept.md.j2`）；
  rename 只是文档欠账，没人在用旧 target 名（生产 `skip_accept: true`，业务 lab 还没接）。
- **为什么 docker-compose 模板不放 helm 之上** —— helm 模板是早期 ttpos-arch-lab 的引用实现，删了
  会让对接 helm 的团队要去翻 git history。两份并列，按团队基础设施挑。
- **为什么不重命名 ENV var `SISYPHUS_STAGE` 取值** —— `SISYPHUS_STAGE=accept-env-up` / `accept-teardown`
  这两个**值** sisyphus 代码是真这么注的（`create_accept.py` / `teardown_accept_env.py`），
  跟 Makefile target 名是两码事；保持代码不动，文档对齐代码即可。
- **为什么不补 `dev-cross-check` 跑 docker-compose target 名校验** —— 不在本 REQ scope；
  accept-env-up/down 是 integration repo 契约，sisyphus 自己不是 integration repo，
  也跑不到这个 target；后续如果有冒烟 lint 需要再开 REQ。
