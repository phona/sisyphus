# Sisyphus ↔ 业务 repo 集成契约

> 业务 repo（source / integration）想接入 sisyphus 流水线，需要满足下面这些契约。
> sisyphus 不调用业务 repo 的内部 API —— 一切走 **Makefile target + git branch + 环境变量 + stdout JSON**。
>
> 想接新 repo？照着 [§4 最小可行模板](#4-最小可行-makefile-模板) 抄。

## 1. 两类 repo 角色

| 角色 | 例子 | 谁起 |
|---|---|---|
| **source repo** | `phona/ttpos-server-go`、`phona/ubox-crosser` | dev-agent 改的代码所在 |
| **integration repo** | `phona/ttpos-arch-lab` | 提供 ephemeral lab env 的 helm chart / 部署脚本 |

source repo 提供 **测试 target**（`ci-test`），integration repo 提供 **环境 target**（`accept-up` / `accept-down`）。

> M15 砍 manifest：sisyphus 不再有"集中式 IDL 描述本 REQ 涉及哪些 repo"。
> 涉及的 repo 信息直接写在 BKD intent issue description 里（人或 analyze-agent 负责），
> 各 stage agent / checker 按需读。sisyphus 只对 **接口层**（Makefile target + branch + tag）做硬约束。

## 2. Makefile target 契约（硬约束）

### 2.1 source repo 必须有

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make ci-test` | staging-test checker（M1） | runner pod，`cd /workspace/source/<repo-name> && make ci-test` | 退码 0 = pass，非 0 = fail |

`make ci-test` 该跑啥（unit / integration / lint / 任何组合）由业务 repo 自己决定。
sisyphus 硬编码只跑这一条命令，业务方在 Makefile 里聚合。

### 2.2 integration repo 必须有（accept 阶段需要时）

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make accept-up` | sisyphus pre-accept（`actions/create_accept.py`） | runner pod，`cd /workspace/integration/<repo-name> && make accept-up` | 1) 退码 0 = lab 起来；2) **stdout 最后一行**是 endpoint JSON（见 §3） |
| `make accept-down` | `actions/teardown_accept_env.py` | 同上 | best-effort，幂等；失败只 warning 不阻塞状态机 |

**幂等性硬要求**：accept-up / accept-down 必须可重复调（重试或 watchdog 路径会再 cleanup 一次）。

## 3. accept-up 的 stdout JSON 契约

`make accept-up` 完成后，**stdout 最后一行**必须是：

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
accept-up:
	@helm upgrade --install lab charts/accept-lab \
	    --namespace $(SISYPHUS_NAMESPACE) --create-namespace \
	    --wait --timeout 5m
	@kubectl -n $(SISYPHUS_NAMESPACE) wait --for=condition=ready pod -l app=lab --timeout=2m
	@printf '{"endpoint": "http://lab.%s.svc.cluster.local:8080", "namespace": "%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$(SISYPHUS_NAMESPACE)"
```

注意结尾的 `\n` —— sisyphus 解析 `result.stdout.splitlines()` 反向第一个非空行，没换行会被外层 shell 吞掉。

## 4. 最小可行 Makefile 模板

### 4.1 source repo (Go 例)

```makefile
.PHONY: ci-test ci-lint ci-build

# sisyphus 只调 ci-test；想跑啥业务自己聚合
ci-test: ci-lint
	go test -race -count=1 ./...
	go test -tags=integration -race -count=1 -timeout=10m ./...

ci-lint:
	go vet ./...
	@which golangci-lint >/dev/null && golangci-lint run ./... || echo "golangci-lint 未装，跳过"

ci-build:
	CGO_ENABLED=0 go build -o bin/$(notdir $(CURDIR)) ./cmd/...
```

### 4.2 integration repo (helm-based 例)

```makefile
.PHONY: accept-up accept-down

# 必须由 sisyphus 注入 SISYPHUS_NAMESPACE；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default

accept-up:
	helm upgrade --install lab charts/accept-lab \
	    --namespace $(SISYPHUS_NAMESPACE) --create-namespace \
	    --wait --timeout 5m
	kubectl -n $(SISYPHUS_NAMESPACE) wait --for=condition=ready pod -l app=lab --timeout=2m
	@printf '{"endpoint": "http://lab.%s.svc.cluster.local:8080", "namespace": "%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$(SISYPHUS_NAMESPACE)"

accept-down:
	-helm uninstall lab --namespace $(SISYPHUS_NAMESPACE) || true
	-kubectl delete namespace $(SISYPHUS_NAMESPACE) --ignore-not-found
```

注意 accept-down 用 `-` 前缀让 make 忽略错误（best-effort 语义）；删 namespace 也带 `--ignore-not-found`。

## 5. 环境变量契约

orchestrator 在 `kubectl exec` 进 runner pod 跑命令时注入：

| env | 何时有 | 含义 / 用法 |
|---|---|---|
| `SISYPHUS_REQ_ID` | 所有 stage | 形如 `REQ-29`，业务 Makefile 拼 namespace / 标签 |
| `SISYPHUS_STAGE` | accept-up / accept-down | `accept-env-up` / `accept-teardown`，给 Makefile 区分阶段 |
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
| spec (×2) | `contract-spec` 或 `acceptance-spec` |
| dev (1~N) | `dev` + `REQ-x`（每个 dev issue 一个；并行时 N 个） |
| accept | `accept` + `result:pass` 或 `result:fail` |
| verifier | `verifier` + `decision:<base64-json>`（见 architecture.md §3） |
| fixer | `fixer` + `fixer:dev\|spec` + `parent-stage:<...>` |

router.py 完全靠 tag 做路由 —— **issue title 不用作判断**。

## 8. 排查清单

接入卡住时按这个顺序看：

1. **staging-test 总是 fail** → `kubectl exec runner-<REQ> -- bash -c "cd /workspace/source/<repo> && make ci-test"` 手跑一遍
2. **pr-ci 永远 timeout** → 查 `feat/REQ-x` 分支真有没有 PR、GHA 在 PR 上确实跑了
3. **accept-up 失败** → 看 stdout 是不是缺最后一行 JSON、`SISYPHUS_NAMESPACE` 是不是被 Makefile 用了
4. **agent 写 tag 没被路由** → 查 `router.derive_event` 对你的 tag 组合返不返事件
