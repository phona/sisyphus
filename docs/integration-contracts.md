# Sisyphus ↔ 业务 repo 集成契约

> 业务 repo（source / integration）想接入 sisyphus 流水线，需要满足下面这些契约。
> sisyphus 不调用业务 repo 的内部 API —— 一切走 **Makefile target + git branch + 环境变量 + stdout JSON**。
>
> 想接新 repo？照着 [§4 最小可行模板](#4-最小可行-makefile-模板) 抄。

## 1. 两类 repo 角色

| 角色 | 例子 | 谁起 |
|---|---|---|
| **source repo** | `phona/ttpos-server-go`、`phona/ubox-crosser` | dev-agent 改的代码所在；**多仓 REQ 平等列表，无主从** |
| **integration repo** | `phona/ttpos-arch-lab` | 提供 ephemeral lab env 的 helm chart / 部署脚本 |

source repo 提供 **ttpos-ci 标准 target**（`ci-lint` / `ci-unit-test` / `ci-integration-test`），
integration repo 提供 **环境 target**（`ci-accept-env-up` / `ci-accept-env-down`）。

> M15 砍 manifest：sisyphus 不再有"集中式 IDL 描述本 REQ 涉及哪些 repo"。
> 涉及的 repo 信息直接写在 BKD intent issue description 里（人或 analyze-agent 负责），
> 各 stage agent / checker 按需读。sisyphus 只对 **接口层**（Makefile target + branch + tag）做硬约束。

> **M16 多仓** —— 移除 `leader_repo_path` 这个隐式契约。所有机械 checker
> 直接遍历 `/workspace/source/*`，不再读 ctx 字段。详见 §"多仓 REQ 协调约定"。

## 1b. 路径约定（**sisyphus 强约定**）

所有 source repo 在 runner pod 里**必须** clone 到 `/workspace/source/<repo-basename>/`，
这是 sisyphus 所有机械 checker（spec_lint / dev_cross_check / staging_test）遍历的根。

```
/workspace/
├── source/<repo-basename>/      ← 每仓一个；basename = github 仓名最后一段
│   ├── .git/
│   ├── Makefile                 ← ci-test / dev-cross-check
│   └── openspec/changes/<REQ>/  ← 该仓的 spec（如本 REQ 改了它）
└── integration/<repo-basename>/ ← lab repo（accept 阶段才用）
```

clone 用 sisyphus 提供的 helper（不要手写 `git clone`）：

```bash
/opt/sisyphus/scripts/sisyphus-clone-repos.sh phona/repo-a phona/repo-b
```

helper 行为：clone 到约定路径、用 `$GH_TOKEN` 做 auth、idempotent
（已存在则 `git fetch && git checkout main`）、shallow + 按需 unshallow。

## 2. Makefile target 契约（硬约束）

### 2.1 source repo 必须有

> 这套契约对齐 **`ttpos-ci`** 标准（`ci-env` / `ci-setup` / `ci-lint` / `ci-unit-test` / `ci-integration-test` / `ci-build`），
> 业务 repo 一份 Makefile 同时供 GitHub Actions 和 sisyphus 调用，**不维护两套 target**。

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make ci-lint` | dev-cross-check checker（M15） | runner pod，**for-each-repo** `cd /workspace/source/<repo> && BASE_REV=$(git merge-base HEAD origin/main) make ci-lint` | go vet + golangci-lint，仅 lint 变更文件（BASE_REV 缺失则全量）；退码 0 = pass |
| `make ci-unit-test` | staging-test checker（M1） | runner pod，**for-each-repo** `cd /workspace/source/<repo> && make ci-unit-test` | 单元测试（业务聚合 main + bmp 等）；退码 0 = pass |
| `make ci-integration-test` | staging-test checker（M1） | 同上 | 集成测试（docker compose 起 stack）；退码 0 = pass |

staging-test checker 单 repo 内 **`ci-unit-test && ci-integration-test` 串行**（避免内存峰值叠加撑爆 pod 8 GiB cgroup），repo 之间并行起。

### 2.2 BASE_REV 约定

dev-cross-check checker 在 runner pod 内每仓计算：

```bash
base_rev=$(git merge-base HEAD origin/main 2>/dev/null \
        || git merge-base HEAD origin/develop 2>/dev/null \
        || git merge-base HEAD origin/dev 2>/dev/null \
        || echo "")
BASE_REV="$base_rev" make ci-lint
```

业务 `ci-lint` 必须接受 `BASE_REV` env 变量（空字符串 = 全量扫描）。golangci-lint
推荐写法：`golangci-lint run ${BASE_REV:+--new-from-rev=$BASE_REV}`，空值时
shell 不展开 flag，等价全量。

### 2.3 integration repo 必须有（accept 阶段需要时）

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make ci-accept-env-up` | sisyphus pre-accept（`actions/create_accept.py`） | runner pod，`cd /workspace/integration/<repo-name> && make ci-accept-env-up` | 1) 退码 0 = lab 起来；2) **stdout 最后一行**是 endpoint JSON（见 §3） |
| `make ci-accept-env-down` | `actions/teardown_accept_env.py` | 同上 | best-effort，幂等；失败只 warning 不阻塞状态机 |

**幂等性硬要求**：ci-accept-env-up / ci-accept-env-down 必须可重复调（重试或 watchdog 路径会再 cleanup 一次）。

## 3. ci-accept-env-up 的 stdout JSON 契约

`make ci-accept-env-up` 完成后，**stdout 最后一行**必须是：

```json
{"endpoint": "http://accept-req-29.svc.cluster.local:8080", "namespace": "accept-req-29"}
```

字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `endpoint` | ✅ | accept-agent 跑 FEATURE-A* scenarios 时打这个 URL |
| `namespace` | （可选） | sisyphus 已通过 `SISYPHUS_NAMESPACE` env 传，重复声明也无碍 |
| 其他 | （可选） | 任意附加元数据，accept-agent prompt 透传 |

实现建议（让 Makefile 容易满足）：

```makefile
ci-accept-env-up:
	@docker compose -p $(SISYPHUS_NAMESPACE) -f accept/docker-compose.yml up -d
	@until curl -sf http://localhost:18080/api/health; do sleep 2; done
	@printf '{"endpoint": "http://localhost:18080", "namespace": "%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)"
```

注意结尾的 `\n` —— sisyphus 解析 `result.stdout.splitlines()` 反向第一个非空行，没换行会被外层 shell 吞掉。

## 4. 最小可行 Makefile 模板

### 4.1 source repo (Go 例，对齐 ttpos-ci 标准)

```makefile
.PHONY: ci-env ci-setup ci-lint ci-unit-test ci-integration-test ci-build

ci-env:
	@echo "GO_VERSION=1.23"
	@echo "NEEDS_DOCKER=true"

ci-setup:
	go mod download
	@which golangci-lint >/dev/null 2>&1 \
	  || curl -sSfL https://raw.githubusercontent.com/golangci/golangci-lint/HEAD/install.sh \
	     | sh -s -- -b $$(go env GOPATH)/bin v1.62.2

# BASE_REV 由 sisyphus 注入；空字符串等价全量
ci-lint:
	go vet ./...
	golangci-lint run $${BASE_REV:+--new-from-rev=$$BASE_REV}

ci-unit-test:
	go test -short -race -count=1 ./...

ci-integration-test:
	docker compose -f tests/docker-compose.yml up --build --exit-code-from test-runner

ci-build:
	CGO_ENABLED=0 go build -o bin/$(notdir $(CURDIR)) ./cmd/...
```

### 4.2 integration repo (Docker Compose 例，sisyphus runner 无 kubectl)

```makefile
.PHONY: ci-accept-env-up ci-accept-env-down

# 必须由 sisyphus 注入 SISYPHUS_NAMESPACE；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default
ACCEPT_IMAGE       ?= ghcr.io/your-org/your-service:latest
ACCEPT_PORT        ?= 18080

ci-accept-env-up:
	@bash accept/env-up.sh

ci-accept-env-down:
	@bash accept/env-down.sh
```

> **注**：sisyphus runner pod 只有 Docker DinD，**没有** kubectl / helm。
> integration repo 应使用 Docker Compose 而非 Helm 管理验收环境。
> 参见 `ttpos-arch-lab` 的 `accept/` 目录作为参考实现。
>
> `ci-accept-env-down` 用 `|| true` / idempotent 脚本处理 best-effort 语义。

## 5. 环境变量契约

orchestrator 在 `kubectl exec` 进 runner pod 跑命令时注入：

| env | 何时有 | 含义 / 用法 |
|---|---|---|
| `SISYPHUS_REQ_ID` | 所有 stage | 形如 `REQ-29`，业务 Makefile 拼 namespace / 标签 |
| `SISYPHUS_STAGE` | ci-accept-env-up / ci-accept-env-down | `accept-env-up` / `accept-teardown`，给 Makefile 区分阶段 |
| `SISYPHUS_NAMESPACE` | accept 阶段 | `accept-<req-id-lowercase>`，专给 helm `-n` 用 |
| `SISYPHUS_RUNNER=1` | runner 镜像内置 | 让脚本能判"我在 sisyphus runner 里" |

**约定**：业务 Makefile 应只依赖上面这些 env；不要假设额外的 `KUBECONFIG` / 凭据 —— 那些是 runner 镜像 / aissh 提供的 ambient context。

## 6. git branch 契约

dev-agent 在 source repo 的 `feat/{REQ-id}` 分支上推代码 + 开 PR。pr-ci-watch checker
按这个分支查 PR：

```
gh pr list --head feat/REQ-29 --repo phona/ttpos-server-go --json number
```

业务 repo 配 GitHub Actions 时建议：

- workflow 必须在 PR 触发时跑（`pull_request` event）
- 至少有一条 `check-run`（哪怕只是 lint）—— 没有 check-run 会 timeout（1800s 默认）
- 失败的 check `conclusion` 用 `failure` / `cancelled` / `timed_out`（pr-ci-watch 认这些）

## 7. tag 契约（agent 报告结果）

stage agent 完成时通过 BKD issue tag 报告结果。详见 [api-tag-management-spec.md](./api-tag-management-spec.md)。常用：

| stage agent | tag |
|---|---|
| analyze | `analyze` |
| spec (1~N) | `spec` + `REQ-x`（每个 spec issue 一个；并行时 N 个） |
| dev (1~N) | `dev` + `REQ-x`（每个 dev issue 一个；并行时 N 个） |
| accept | `accept` + `result:pass` 或 `result:fail` |
| verifier | `verifier` + `decision:<base64-json>`（见 architecture.md §3） |
| fixer | `fixer` + `fixer:dev\|spec` + `parent-stage:<...>` |

router.py 完全靠 tag 做路由 —— **issue title 不用作判断**。

## 8. 排查清单

接入卡住时按这个顺序看：

1. **staging-test 总是 fail** → `kubectl exec runner-<REQ> -- bash -c "cd /workspace/source/<repo> && make ci-unit-test && make ci-integration-test"` 手跑一遍
2. **spec-lint 总是 fail** → `kubectl exec runner-<REQ> -- bash -c "for r in /workspace/source/*; do [ -d \$r/openspec/changes/<REQ> ] && (cd \$r && openspec validate openspec/changes/<REQ>); done"` 手跑
3. **pr-ci 永远 timeout** → 查 `feat/REQ-x` 分支真有没有 PR、GHA 在 PR 上确实跑了
4. **ci-accept-env-up 失败** → 看 stdout 是不是缺最后一行 JSON、`SISYPHUS_NAMESPACE` 是不是被 Makefile 用了；检查 `docker compose logs` 确认 server-go 启动成功
5. **agent 写 tag 没被路由** → 查 `router.derive_event` 对你的 tag 组合返不返事件
6. **多仓 checker 抱怨某仓没找到** → 看 `/workspace/source/<basename>/` 目录在不在；
   `sisyphus-clone-repos.sh` 调过吗；basename 跟 BKD intent description 列的是否一致

## 9. 多仓 REQ 协调约定（M16）

一个 REQ 经常涉及多个 source repo（典型：前端 + 后端、producer + consumer、
core lib + caller）。M16 起 sisyphus 把多仓当**平等列表**，**不再有 leader 主从概念**。

### 9.1 spec 文档归属

每个被改的 source repo **自带** `openspec/changes/{{ "REQ-x" }}/` —— 不再集中放
"leader 仓"。analyze-agent 在每仓写 proposal.md / tasks.md。

```
phona/repo-a/openspec/changes/REQ-29/
  ├── proposal.md          # repo-a 角度的需求
  ├── tasks.md
  └── specs/...

phona/repo-b/openspec/changes/REQ-29/
  ├── proposal.md          # repo-b 角度的需求
  ├── tasks.md
  └── specs/...
```

### 9.2 跨仓 contract.spec.yaml 的归属

如果 repo-a 的 service 调 repo-b 暴露的 endpoint：

- **contract.spec.yaml 写在 producer 仓**（提供 endpoint 的那一方 = repo-b）
  路径：`phona/repo-b/openspec/changes/REQ-x/contracts/<endpoint>.spec.yaml`
- consumer 仓只**引用** producer scenario ID，不重复定义 contract
- 哪一方是 producer：**API 提供方 = producer**（不管谁先发起调用）

### 9.3 scenario ID 命名空间

多仓 REQ scenario ID **必须** 用 `[<REPO>-S<N>]` 前缀防撞（REPO 用大写 basename）：

- ✅ `[REPO-A-S1] login endpoint returns JWT`
- ✅ `[REPO-B-S1] user table has unique email constraint`
- ❌ `S1 ...`（多仓不带前缀容易撞）

单仓 REQ 可用裸 `S<N>`，scenario 数量少时不强制。

### 9.4 跨仓 scenario 引用

`check-scenario-refs.sh` 接受 `--specs-search-path <path>[,<path>...]` flag
让 lint 把其他仓的 `openspec/specs/` 也加进搜索路径。spec_lint checker 自动
用所有仓的 specs 互引：

```bash
check-scenario-refs.sh /workspace/source/repo-a \
  --specs-search-path /workspace/source/repo-b/openspec/specs
```

### 9.5 spec home repo（弱归属，仅 done_archive 用）

跨仓 REQ 经常有"全 REQ 共享"的高层文档（proposal、design、跨仓集成总览）只想写
一份不想抄 N 遍。约定**指定其中一个仓为 "spec home repo"**，把这种共享文档
放在它的 `openspec/changes/REQ-x/` 下。done_archive 时该仓 `openspec apply`
会把它一起归档。

**怎么声明**：在 BKD intent issue chat 里直说一句：

> spec home repo: phona/repo-a

不是 ctx 字段、不是机器化结构 —— done_archive agent 看 description 自己识别。
单仓 REQ 默认就是它自己。

### 9.6 dev / spec agent 默认按仓拆

analyze-agent 默认拆并行：每个被改的 source repo 起一个 `tag=dev + REQ-x`
BKD issue（每个 issue prompt 写明 target repo + scope）。spec 同理按需拆
（默认一个 spec-agent 写所有仓 OK）。

### 9.7 verifier decision 的 `target_repo`

多仓 REQ 任一 checker 失败时，verifier 要在 decision JSON 里加可选 `target_repo`
字段告诉 fixer 去哪修。stderr 里 sisyphus 用 `=== FAIL: <basename> ===` 标记
失败仓让 verifier 容易识别。详见 [architecture.md §3](./architecture.md)。
