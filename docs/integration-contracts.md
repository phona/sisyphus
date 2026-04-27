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
integration repo 提供 **环境 target**（`accept-env-up` / `accept-env-down`）。

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

## 1c. Runner GitHub PAT（**只读**）

> **设计契约**：runner pod 只用 `$GH_TOKEN` 做 `git clone`（读私有仓）。
> 所有 `git push` / `gh pr create` / `gh pr merge` 都在 BKD Coder workspace
> （"开发机"）里跑，用 Coder 自己注入的 gh auth，**跟这个 PAT 无关**。
> 详见 [architecture.md §8](./architecture.md#8-runnerk8s-pod--pvcper-req)。

### 1c.1 PAT 选型（按推荐度）

| 类型 | scope / 权限 | 跨 org? | 推荐度 |
|---|---|---|---|
| **Fine-grained PAT**（org 内生成） | Repository: 选具体仓；Permissions → **Contents: Read-only**（+ Packages: Read-only 如需拉 ghcr 私有镜像） | 限 1 org | ⭐⭐⭐ 最干净 |
| **Fine-grained PAT**（user 生成 + org 授权） | 同上，但 resource owner 选 org | 同左 | ⭐⭐ |
| **Classic PAT** | 必勾 `repo`（GitHub 不细分；含 r+w，runner 不会用 write 那部分） + `read:org`（看 org 成员关系，debug 用） + `read:packages`（如需） | 跨 org，scope 范围大 | ⭐ 临时凑合，过期短一些 |

### 1c.2 验证 playbook（patch K8s secret 之前必跑）

GitHub 对私有仓返 403/404 时文案有歧义（"Write access not granted" 实际可能是 read 也没权限），
patch secret 前必须**先用 API 探一遍**：

```bash
PAT='ghp_xxx'   # 千万别 echo 进 transcript / commit 进代码

# 1. PAT 能 auth + 实际 scope 是什么
curl -sS -D - -o /dev/null -H "Authorization: token $PAT" https://api.github.com/user \
  | grep -iE "^(x-oauth-scopes|github-authentication-token-expiration)"
# 期望：x-oauth-scopes 含 "repo"（classic）或为空但 fine-grained permissions 配对了
#       expiration 还没过

# 2. PAT user 加入了哪些 org
curl -sS -H "Authorization: token $PAT" https://api.github.com/user/orgs \
  | python3 -c "import json,sys; print([o['login'] for o in json.load(sys.stdin)])"
# 期望：含目标 org（如 ZonEaseTech）；空 [] 八成是 user 不在 org 里 OR 没勾 read:org

# 3. PAT 能看到目标私有仓
curl -sS -o /tmp/r -w "HTTP=%{http_code}\n" -H "Authorization: token $PAT" \
  https://api.github.com/repos/<org>/<repo>
python3 -c "import json; d=json.load(open('/tmp/r')); \
  print(d.get('message') or f\"OK private={d.get('private')} permissions={d.get('permissions')}\")"
# 期望：HTTP=200，permissions.pull=true（push 不需要也无害）
# HTTP=404 = PAT 不可见该仓（user 不在 org / 没访问权限 / scope 不够）

# 4. git 实测（runner 视角）
git ls-remote https://x-access-token:$PAT@github.com/<org>/<repo>.git HEAD
# 期望：返 SHA + HEAD；返 fatal/403 = 还有问题
```

四步全过才 patch K8s secret：

```bash
TOKEN='ghp_xxx'
kubectl -n sisyphus-runners patch secret sisyphus-runner-secrets --type=merge \
  -p "{\"stringData\":{\"gh_token\":\"$TOKEN\",\"ghcr_token\":\"$TOKEN\"}}"
unset TOKEN
```

### 1c.3 常见误诊

| 报错 | 实际意思 |
|---|---|
| `git clone ... 403: Write access to repository not granted` | GitHub 通用文案，**不一定**是写权限不够；可能是 PAT 完全没读权限（scope 没勾 / user 不在 org） |
| `repos/<org>/<repo>` 返 404（仓真实存在） | PAT 没有 `repo` scope OR user 不在 org；GitHub 隐藏私有仓存在 |
| `/user/orgs` 返 `[]` | (a) user 真没 org，OR (b) classic PAT 没勾 `read:org` —— 加 scope 重测 |
| PAT patch 后 runner 仍 403 | runner pod 是 patch **之前**起的，env 不会回传；删 pod 让新 pod 拿新 secret，或派新 REQ |

### 1c.4 patch 完用 dogfood REQ 验

派一个 `intent:analyze` + `repo:<org>/<target-repo>` tag 的 BKD issue，
看 orch 日志 `clone.exec` → `clone.done`（成功）或 `clone.failed`（失败原因）。
**不要直接 helm rollout** —— 部分 cluster `--reuse-values` 模式下 secret patch 不需要 rollout。

## 2. Makefile target 契约（硬约束）

### 2.1 source repo 必须有

> 这套契约对齐 **`ttpos-ci`** 标准（`ci-env` / `ci-setup` / `ci-lint` / `ci-unit-test` / `ci-integration-test` / `ci-build`），
> 业务 repo 一份 Makefile 同时供 GitHub Actions 和 sisyphus 调用，**不维护两套 target**。

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make ci-lint` | dev-cross-check checker（M15） | runner pod，**for-each-repo** `cd /workspace/source/<repo> && BASE_REV=$(git merge-base HEAD origin/<default_branch>) make ci-lint`（default_branch 先 resolve `origin/HEAD` 符号引用，再退 main/master/develop/dev） | go vet + golangci-lint，仅 lint 变更文件（BASE_REV 缺失则全量）；退码 0 = pass |
| `make ci-unit-test` | staging-test checker（M1） | runner pod，**for-each-repo** `cd /workspace/source/<repo> && make ci-unit-test` | 单元测试（业务聚合 main + bmp 等）；退码 0 = pass |
| `make ci-integration-test` | staging-test checker（M1） | 同上 | 集成测试（docker compose 起 stack）；退码 0 = pass |

staging-test checker 单 repo 内 **`ci-unit-test && ci-integration-test` 串行**（避免内存峰值叠加撑爆 pod 8 GiB cgroup），repo 之间并行起。

### 2.2 BASE_REV 约定

dev-cross-check checker 在 runner pod 内每仓计算（先读仓**真实** default_branch，
再退静态链；REQ-fix-base-rev-default-branch-1777214183）：

```bash
default_branch=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null \
                 | sed 's@^origin/@@' || true)
base_rev=$(([ -n "$default_branch" ] && git merge-base HEAD "origin/$default_branch" 2>/dev/null) \
        || git merge-base HEAD origin/main 2>/dev/null \
        || git merge-base HEAD origin/master 2>/dev/null \
        || git merge-base HEAD origin/develop 2>/dev/null \
        || git merge-base HEAD origin/dev 2>/dev/null \
        || echo "")
BASE_REV="$base_rev" make ci-lint
```

`origin/HEAD` 符号引用由 `git clone` 自动设置，指向 GitHub repo 当时的默认分支。
默认分支非 main / master / develop / dev 的仓（ttpos-server-go / ttpos-flutter
默认 `release`）以前整条链全 miss → BASE_REV 为空 → ci-lint 退化全量扫；
现在先读 `origin/HEAD` 拿到 `release`，正确计算 merge-base。

业务 `ci-lint` 必须接受 `BASE_REV` env 变量（空字符串 = 全量扫描）。golangci-lint
推荐写法：`golangci-lint run ${BASE_REV:+--new-from-rev=$BASE_REV}`，空值时
shell 不展开 flag，等价全量。

### 2.3 integration repo 必须有（accept 阶段需要时）

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make accept-env-up` | sisyphus pre-accept（`actions/create_accept.py`） | runner pod，`cd /workspace/integration/<repo-name> && make accept-env-up` | 1) 退码 0 = lab 起来；2) **stdout 最后一行**是 endpoint JSON（见 §3） |
| `make accept-env-down` | `actions/teardown_accept_env.py` | 同上 | best-effort，幂等；失败只 warning 不阻塞状态机 |

**幂等性硬要求**：accept-env-up / accept-env-down 必须可重复调（重试或 watchdog 路径会再 cleanup 一次）。

> **历史命名**：早期契约依次叫过 `accept-up` / `accept-down`（M14 之前）和
> `ci-accept-env-up` / `ci-accept-env-down`（REQ-accept-contract-docs-1777121224）。
> 当前唯一支持名为 `accept-env-up` / `accept-env-down`（REQ-rename-accept-targets-1777124774，
> 去掉了误导性的 `ci-` 前缀 —— accept env 不在 PR-CI 热路径上）。
> 老 integration repo 接入时改 target 名到这套就行 —— sisyphus 代码（`create_accept.py` /
> `teardown_accept_env.py`）只调 `accept-env-up` / `accept-env-down`，前两代旧名不再触发。

## 3. accept-env-up 的 stdout JSON 契约

`make accept-env-up` 完成后，**stdout 最后一行**必须是：

```json
{"endpoint": "http://accept-req-29.svc.cluster.local:8080", "namespace": "accept-req-29"}
```

字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `endpoint` | ✅ | accept-agent 跑 FEATURE-A* scenarios 时打这个 URL |
| `namespace` | （可选） | sisyphus 已通过 `SISYPHUS_NAMESPACE` env 传，重复声明也无碍 |
| `thanatos` | （可选） | thanatos M1 起：业务仓 `accept-env-up` 起了 thanatos pod 时填 |
| 其他 | （可选） | 任意附加元数据，accept-agent prompt 透传 |

`thanatos` 子 object（thanatos M1 起，[REQ-415](../openspec/changes/REQ-415/proposal.md)）：

| 子字段 | 必需 | 说明 |
|---|---|---|
| `pod` | ✅ | accept-agent `kubectl exec` 进它跑 `python -m thanatos.server` 喂 stdio MCP |
| `namespace` | （可选） | 默认顶层 `namespace`；thanatos 单独装到其他 namespace 才显式覆盖 |
| `skill_repo` | ✅ | source repo basename，accept-agent 从 `/workspace/source/<skill_repo>/.thanatos/` 取 skill 给 MCP `run_all` 用 |

`thanatos` 缺省 → accept-agent 走老路（直接 curl endpoint 跑 scenario）。sisyphus
自家 `deploy/accept-compose.yml` 不接 thanatos，自动走 fallback 分支。

带 thanatos block 的 sample：

```json
{
  "endpoint": "http://lab.accept-req-415.svc.cluster.local:8080",
  "namespace": "accept-req-415",
  "thanatos": {
    "pod": "thanatos-7d8f8d8f8-abcde",
    "namespace": "accept-req-415",
    "skill_repo": "ttpos-flutter"
  }
}
```

实现建议（让 Makefile 容易满足）：

```makefile
accept-env-up:
	@helm upgrade --install lab charts/accept-lab \
	    --namespace $(SISYPHUS_NAMESPACE) --create-namespace \
	    --wait --timeout 5m
	@kubectl -n $(SISYPHUS_NAMESPACE) wait --for=condition=ready pod -l app=lab --timeout=2m
	@printf '{"endpoint": "http://lab.%s.svc.cluster.local:8080", "namespace": "%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$(SISYPHUS_NAMESPACE)"
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

### 4.2 integration repo (helm-based 例)

> 想看完整的 **「mobile App + 后端 stack」端到端 lab** 食谱（helm 部 backend + emulator
> 到 K3s + 编 APK + 装到 emulator + 多键 endpoint JSON + boot-wait），
> 看 [`docs/cookbook/ttpos-arch-lab-accept-env.md`](cookbook/ttpos-arch-lab-accept-env.md)。
> 这里的 §4.2 只是纯后端 stack 的最小骨架。

```makefile
.PHONY: accept-env-up accept-env-down

# 必须由 sisyphus 注入 SISYPHUS_NAMESPACE；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default

accept-env-up:
	helm upgrade --install lab charts/accept-lab \
	    --namespace $(SISYPHUS_NAMESPACE) --create-namespace \
	    --wait --timeout 5m
	kubectl -n $(SISYPHUS_NAMESPACE) wait --for=condition=ready pod -l app=lab --timeout=2m
	@printf '{"endpoint": "http://lab.%s.svc.cluster.local:8080", "namespace": "%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$(SISYPHUS_NAMESPACE)"

accept-env-down:
	-helm uninstall lab --namespace $(SISYPHUS_NAMESPACE) || true
	-kubectl delete namespace $(SISYPHUS_NAMESPACE) --ignore-not-found
```

注意 accept-env-down 用 `-` 前缀让 make 忽略错误（best-effort 语义）；删 namespace 也带 `--ignore-not-found`。

### 4.2.2 integration repo (docker-compose 例)

不所有团队都在 K3s 部 lab —— 用 docker compose stack 跑端到端的 integration repo
按下面这份模板抄。约定跟 helm 那份一样：`accept-env-up` 起 stack 并在 stdout
**最后一行**吐 endpoint JSON；`accept-env-down` 幂等清理。

> 想看完整的 **「mobile App + 后端 stack」docker-compose 路径** 食谱（compose 起 backend +
> headless Android emulator + 编 APK），
> 看 [`docs/cookbook/ttpos-arch-lab-accept-env.md`](cookbook/ttpos-arch-lab-accept-env.md) §9 说明。
> **推荐改用 helm 路径**（见 §4.2 和 cookbook 正文）—— namespace 天然隔离，无 host port 碰撞风险。
> 这里的 §4.2.2 只是纯后端 stack 的最小骨架，适用于不在 K3s 上的 lab 环境。
>
> 想看 **Flutter 源仓自承 integration repo**（不依赖 arch-lab、不起 emulator、纯 HTTP 黑盒）
> 的完整食谱（决策树、`tests/docker-compose.accept.yml` 设计、accept-agent scenario 限制、
> 排查清单），看 [`docs/cookbook/ttpos-flutter-mobile-accept-env.md`](cookbook/ttpos-flutter-mobile-accept-env.md)。

```makefile
.PHONY: accept-env-up accept-env-down

# 必须由 sisyphus 注入 SISYPHUS_NAMESPACE；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default
COMPOSE_PROJECT_NAME := $(SISYPHUS_NAMESPACE)
COMPOSE_FILE ?= docker-compose.accept.yml

accept-env-up:
	docker compose -p $(COMPOSE_PROJECT_NAME) -f $(COMPOSE_FILE) up -d --wait
	@port=$$(docker compose -p $(COMPOSE_PROJECT_NAME) -f $(COMPOSE_FILE) port lab 8080 | awk -F: 'END{print $$NF}'); \
	if [ -z "$$port" ]; then \
	    echo >&2 "accept-env-up: cannot resolve published port for service 'lab' :8080 in compose project $(COMPOSE_PROJECT_NAME)"; \
	    exit 1; \
	fi; \
	printf '{"endpoint": "http://localhost:%s", "namespace": "%s"}\n' "$$port" "$(SISYPHUS_NAMESPACE)"

accept-env-down:
	-docker compose -p $(COMPOSE_PROJECT_NAME) -f $(COMPOSE_FILE) down --volumes --remove-orphans || true
```

要点：

- **endpoint 从 `docker compose port` 取宿主端口**，不要硬编码 `8080`，避免不同 REQ
  并发跑同一 host 时端口撞车（`docker compose -p $SISYPHUS_NAMESPACE` 已经隔离了网络
  + 容器名空间，但宿主端口是 host 资源，得让 compose 自分配）。
- **`up -d --wait`**：`--wait` 让 `docker compose` 阻塞到所有 service 的 healthcheck
  转 healthy（compose v2.20+），等价 helm `--wait`。service 必须在 compose 文件里
  声明 `healthcheck`，否则 `--wait` 仅等容器 running，跟 lab 真就绪是两回事。
- **`down --volumes`**：拆环境时一并清 named volumes，**避免 REQ 间 stale 数据库
  影响下一次 `accept-env-up`**。如有真要保留的卷（trace 数据 / debug 镜像），改为
  `--remove-orphans` 不带 `--volumes`，自己另收。
- **service 名硬约定 `lab`**：`docker compose port lab 8080` 假设业务 stack 里有
  名为 `lab` 的 service 暴露 8080。如果业务用别的名字，在模板里相应改。

`docker-compose.accept.yml` 最小骨架（参考）：

```yaml
services:
  lab:
    image: ghcr.io/<org>/<repo>:<tag>
    ports:
      - "8080"          # 不写 host port → docker 自动分配
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/healthz"]
      interval: 5s
      timeout: 2s
      retries: 12
      start_period: 5s
```

## 5. 环境变量契约

orchestrator 在 `kubectl exec` 进 runner pod 跑命令时注入：

| env | 何时有 | 含义 / 用法 |
|---|---|---|
| `SISYPHUS_REQ_ID` | 所有 stage | 形如 `REQ-29`，业务 Makefile 拼 namespace / 标签 |
| `SISYPHUS_STAGE` | accept-env-up / accept-env-down | `accept-env-up` / `accept-teardown`，给 Makefile 区分阶段 |
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
4. **accept-env-up 失败** → 看 stdout 是不是缺最后一行 JSON、`SISYPHUS_NAMESPACE` 是不是被 Makefile 用了
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
