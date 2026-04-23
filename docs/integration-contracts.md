# Sisyphus ↔ 业务 repo 集成契约

> 业务 repo（source / integration）想接入 sisyphus 流水线，需要满足下面这些契约。
> sisyphus 不调用业务 repo 的内部 API —— 一切走 **Makefile target + 环境变量 + stdout JSON**。
>
> 想接新 repo？照着 [§5 最小可行模板](#5-最小可行-makefile-模板) 抄。

## 1. 两类 repo 角色

| 角色 | 例子 | 在 manifest 里 | 谁起 |
|---|---|---|---|
| **source repo** | `phona/ttpos-server-go`、`phona/ubox-crosser` | `sources[]` 至少 1 个 leader + 0~N follower | dev-agent 改的代码所在 |
| **integration repo** | `phona/ttpos-arch-lab` | `integration` (单个) | 提供 ephemeral lab env 的 helm chart / 部署脚本 |

source repo 提供 **测试 target**（unit / integration），integration repo 提供 **环境 target**（env-up / env-down）。

## 2. Makefile target 契约

### 2.1 source repo 必须有

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make ci-unit-test` | staging-test checker（M1） | runner pod，`cd /workspace/<source.path> && make ci-unit-test` | 退码 0 = pass，非 0 = fail |
| `make ci-integration-test` | 同上（被 manifest.test.cmd 拼） | 同上 | 同上 |
| `make ci-lint` | （可选）dev fixer 验证用 | 同上 | 同上 |
| `make ci-build` | （可选）dev / accept image 构建用 | 同上 | 退码 0 + 产物落到约定路径 |

实际跑什么命令由 **manifest.test.cmd** 决定，sisyphus 不硬编码。analyze-agent 在写 manifest 时按 repo 实际能力填，例如：

```yaml
test:
  cmd: make ci-unit-test ci-integration-test
  cwd: source/ttpos-server-go
  timeout_sec: 600
```

### 2.2 integration repo 必须有

| target | 谁调 | 在哪跑 | 期望 |
|---|---|---|---|
| `make ci-accept-env-up` | sisyphus pre-accept（`actions/create_accept.py`） | runner pod，`cd /workspace/integration/* && make ci-accept-env-up` | 1) 退码 0 = lab 起来；2) **stdout 最后一行**是 endpoint JSON（见 §3） |
| `make ci-accept-env-down` | `actions/teardown_accept_env.py` | 同上 | best-effort，幂等；失败只 warning 不阻塞状态机 |

**幂等性硬要求**：env-up / env-down 必须可重复调（重试或 watchdog 路径会再 cleanup 一次）。

## 3. accept env-up 的 stdout JSON 契约

`make ci-accept-env-up` 完成后，**stdout 最后一行**必须是：

```json
{"endpoint": "http://accept-req-29.svc.cluster.local:8080", "image_tags": {"...": "..."}, "namespace": "accept-req-29"}
```

字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `endpoint` | ✅ | accept-agent 跑 FEATURE-A* scenarios 时打这个 URL |
| `namespace` | （可选） | sisyphus 已经通过 `SISYPHUS_NAMESPACE` env 传，重复声明也无碍 |
| `image_tags` | （可选） | dev / pr-ci stage 写入 `manifest.image_tags`，env-up 据此 helm install |
| 其他 | （可选） | 任意附加元数据，accept-agent prompt 透传 |

实现建议（让 Makefile 容易满足）：

```makefile
ci-accept-env-up:
	@helm upgrade --install lab charts/accept-lab \
	    --namespace $(SISYPHUS_NAMESPACE) --create-namespace \
	    --wait --timeout 5m
	@kubectl -n $(SISYPHUS_NAMESPACE) wait --for=condition=ready pod -l app=lab --timeout=2m
	@printf '{"endpoint": "http://lab.%s.svc.cluster.local:8080", "namespace": "%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$(SISYPHUS_NAMESPACE)"
```

注意结尾的 `\n` —— sisyphus 解析 `result.stdout.splitlines()` 反向第一个非空行，没换行会被外层 shell 吞掉。

## 4. 环境变量契约

orchestrator 在 `kubectl exec` 进 runner pod 跑这些命令时注入：

| env | 何时有 | 含义 / 用法 |
|---|---|---|
| `SISYPHUS_REQ_ID` | 所有 stage | 形如 `REQ-29`，业务 Makefile 拼 namespace / 标签 |
| `SISYPHUS_STAGE` | accept env-up / teardown | `accept-env-up` / `accept-teardown`，给 Makefile 区分阶段 |
| `SISYPHUS_NAMESPACE` | accept 阶段 | `accept-<req-id-lowercase>`，专给 helm `-n` 用 |
| `SISYPHUS_RUNNER=1` | runner 镜像内置 | 让脚本能判"我在 sisyphus runner 里" |

**约定**：业务 Makefile 应只依赖上面这些 env；不要假设额外的 `KUBECONFIG` / 凭据 —— 那些是 runner 镜像 / aissh 提供的 ambient context。

## 5. 最小可行 Makefile 模板

### 5.1 source repo (Go 例)

```makefile
.PHONY: ci-lint ci-unit-test ci-integration-test ci-build

ci-lint:
	go vet ./...
	@which golangci-lint >/dev/null && golangci-lint run ./... || echo "golangci-lint 未装，跳过"

ci-unit-test:
	go test -race -count=1 ./...

# integration test 需要起依赖（DB 等）；这里假设走 testcontainers 或 docker compose
ci-integration-test:
	go test -tags=integration -race -count=1 -timeout=10m ./...

ci-build:
	CGO_ENABLED=0 go build -o bin/$(notdir $(CURDIR)) ./cmd/...
```

### 5.2 integration repo (helm-based 例)

```makefile
.PHONY: ci-accept-env-up ci-accept-env-down

# 必须由 sisyphus 注入 SISYPHUS_NAMESPACE；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default

ci-accept-env-up:
	helm upgrade --install lab charts/accept-lab \
	    --namespace $(SISYPHUS_NAMESPACE) --create-namespace \
	    --wait --timeout 5m
	kubectl -n $(SISYPHUS_NAMESPACE) wait --for=condition=ready pod -l app=lab --timeout=2m
	@printf '{"endpoint": "http://lab.%s.svc.cluster.local:8080", "namespace": "%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$(SISYPHUS_NAMESPACE)"

ci-accept-env-down:
	-helm uninstall lab --namespace $(SISYPHUS_NAMESPACE) || true
	-kubectl delete namespace $(SISYPHUS_NAMESPACE) --ignore-not-found
```

注意 env-down 用 `-` 前缀让 make 忽略错误（best-effort 语义）；删 namespace 也带 `--ignore-not-found`。

## 6. manifest.yaml 契约

analyze-agent 写、admission 校、staging-test / pr-ci-watch / accept env 读。schema 在 [orchestrator/src/orchestrator/schemas/manifest.json](../orchestrator/src/orchestrator/schemas/manifest.json)（draft-07）。

业务 repo 接入时不直接写 manifest（agent 写），但需要知道 manifest 里跟你 repo 相关的字段长啥样：

```yaml
schema_version: 1
req_id: REQ-29

sources:
  - repo: phona/ttpos-server-go    # owner/name 形式
    path: source/ttpos-server-go    # 必须 source/ 前缀
    role: leader                    # 主 repo；恰好 1 个
    branch: stage/REQ-29            # 必须 stage/ 前缀
    depends_on: []                  # 可选：声明 build 依赖

integration:                        # 可选；只 accept 阶段用
  repo: phona/ttpos-arch-lab
  path: integration/ttpos-arch-lab  # 必须 integration/ 前缀

test:
  cmd: make ci-unit-test ci-integration-test
  cwd: source/ttpos-server-go       # 相对 /workspace
  timeout_sec: 600                  # 30~3600

pr:
  repo: phona/ttpos-server-go       # 通常等于 leader source repo
  number: 123                       # dev-agent 开 PR 后回写
```

跨字段校验（`manifest_validate.py` 内置）：

- 恰好 1 个 `sources[].role=leader`
- `sources[].path` 必须以 `source/` 开头
- `integration.path` 必须以 `integration/` 开头
- 同一 `repo` 不能在 `sources[]` 出现两次

## 7. PR 契约

dev-agent 真开 PR（PR #17 起强制）+ 把 PR number 回写 `manifest.pr.number`。pr-ci-watch 然后调 GitHub REST：

- `GET /repos/{repo}/pulls/{number}` → 取 head SHA
- `GET /repos/{repo}/commits/{sha}/check-runs` → 轮询 conclusion

业务 repo 配 GitHub Actions 时建议：

- workflow 必须在 PR 触发时跑（`pull_request` event）
- 至少有一条 `check-run`（哪怕只是 lint）—— 没有 check-run 会 timeout（1800s 默认）
- 失败的 check `conclusion` 用 `failure` / `cancelled` / `timed_out`（pr-ci-watch 认这些）

## 8. tag 契约（agent 报告结果）

stage agent 完成时通过 BKD issue tag 报告结果。详见 [api-tag-management-spec.md](./api-tag-management-spec.md)。常用：

| stage agent | tag |
|---|---|
| analyze | `analyze` |
| spec (×2) | `contract-spec` 或 `acceptance-spec` |
| dev | `dev` |
| accept | `accept` + `result:pass` 或 `result:fail` |
| verifier | `verifier` + `decision:<base64-json>`（见 architecture.md §3） |
| fixer | `fixer` + `fixer:dev|spec|manifest` + `parent-stage:<...>` |

router.py 完全靠 tag 做路由 —— **issue title 不用作判断**。

## 9. 排查清单

接入卡住时按这个顺序看：

1. **manifest schema 不通过** → 看 admission 报错，对比 [schemas/manifest.json](../orchestrator/src/orchestrator/schemas/manifest.json)
2. **staging-test 总是 fail** → `kubectl exec runner-<REQ> -- bash -c "cd /workspace/<test.cwd> && <test.cmd>"` 手跑一遍
3. **pr-ci 永远 timeout** → 查 `manifest.pr.repo` / `pr.number`、查 GHA 在 PR 上确实跑了
4. **accept env-up 失败** → 看 stdout 是不是缺最后一行 JSON、`SISYPHUS_NAMESPACE` 是不是被 Makefile 用了
5. **agent 写 tag 没被路由** → 查 `router.derive_event` 对你的 tag 组合返不返事件
