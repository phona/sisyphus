# Repo 接入 Sisyphus 指南（v0.2）

给 repo 维护方看的清单：要让 sisyphus 能编排一个 REQ 跑完整开发流程，repo 需要满足下面这些约定。

---

## 1. Workspace 约定

sisyphus 在每个 REQ 的 runner Pod 里给你一个 PVC 挂 `/workspace`，分析阶段 `analyze-agent` 负责初始化成下面的结构：

```
/workspace/
├── .sisyphus/
│   └── manifest.yaml           # 本 REQ 的拓扑 + 运行时元信息（分析阶段建，后续阶段增量更新）
├── source/
│   ├── <source-repo-1>/        # 每个 source repo 一个 subdir（名字 = repo name）
│   └── <source-repo-2>/
└── integration/                # 如果 REQ 涉及集成环境（有 lab）
    └── <integration-repo>/
```

- `source/` 放**本 REQ 要改代码**的 repo
- `integration/` 放**拉起调试/验收环境**的 lab repo（只有 `accept` 阶段的 Makefile target 在里面跑）；不涉及 lab 的 REQ 此目录为空
- `.sisyphus/manifest.yaml` 是 agent 间交流的唯一状态文件

---

## 2. `.sisyphus/manifest.yaml` 结构

```yaml
# 由 analyze-agent 生成；后续 stage 增量补充字段（不覆盖其他字段）
schema_version: 1
req_id: REQ-997

# 本 REQ 要改动的 source repo（按 role 排序）
sources:
  - repo: phona/ttpos-server-go      # owner/name
    path: source/ttpos-server-go     # 相对 /workspace 的 subdir
    role: leader                      # leader | source；leader 是业务主 repo
    branch: stage/REQ-997-dev         # dev agent push 的分支
    depends_on: [phona/ubox-proto]    # merge 依赖（可选，仅 leader/中间层声明）

  - repo: phona/ubox-proto
    path: source/ubox-proto
    role: source
    branch: stage/REQ-997-dev

# 可选：接集成环境（lab）的 repo
integration:
  repo: phona/ttpos-arch-lab
  path: integration/ttpos-arch-lab

# ── 下面字段由后续 stage 填（analyze 生成时可留空或忽略）──

# dev 阶段 push 完后填每个 source 的最新 SHA + PR URL
sha_by_repo: {}
pr_by_repo: {}

# pr-ci-watch agent 全绿时填每个 source repo 的 image_tag
image_tags: {}

# done_archive 时 agent 按 sources.depends_on 推算的合并顺序
merge_order: []
```

**schema 严格**：字段名 / 类型不能乱。sisyphus 在 staging 起手前会调
`/opt/sisyphus/scripts/validate-manifest.py` 验证，不合法直接 escalate。

---

## 3. 必备 Makefile target

sisyphus 在各阶段通过 `kubectl exec` 调 Makefile：

| target | 调用阶段 | 职责 | 失败处理 |
|---|---|---|---|
| `ci-lint` | staging-test | go vet + golangci-lint | fail → bug:pre-release |
| `ci-unit-test` | staging-test | 单测（各 source repo 分别跑）| fail → bug:pre-release |
| `ci-integration-test` | staging-test | docker-compose 起 stack 跑 contract test | fail → bug:pre-release |
| `ci-build` | PR CI（在 GHA 里跑，不在 sisyphus）| build + push image | fail → bug:ci |
| `ci-accept-env-up` | accept 前（在 integration repo 根目录跑）| helm install lab 到 accept-<REQ> ns，stdout 尾行输出 JSON `{"endpoint":"..."}` | fail → escalate（lab 起不来）|
| `ci-accept-env-down` | accept 后（teardown，必跑）| helm uninstall lab，idempotent | fail 只 warning，不阻塞 |

**注意**：`ci-accept-*` 只在 `integration/` 下的 integration repo 需要。纯 source repo 不用配。

---

## 4. sisyphus 注入的环境变量

所有 Makefile target 通过 `kubectl exec env=...` 拿到：

| env | 意义 |
|---|---|
| `SISYPHUS_REQ_ID` | REQ-N |
| `SISYPHUS_STAGE` | staging-test / pr-ci / accept |
| `SISYPHUS_NAMESPACE` | 仅 accept 阶段：accept-<req-id>（K8s ns）|
| `SISYPHUS_IMAGE_TAGS` | 仅 accept 阶段：JSON dict `{"<repo>":"<image_tag>"}` |
| `SISYPHUS_FEATURES_FILE` | 仅 accept 阶段：FEATURE-A* 列表 yaml 文件路径 |
| `KUBECONFIG` | `/root/.kube/config`（通过 runner Pod 的 Secret 挂载）|
| `DOCKER_CONFIG` | GHCR 登录凭证（ghcr.io pull auth）|
| `GH_TOKEN` | GitHub PAT（gh CLI 用）|

---

## 5. `.claude/skills/` 要求

sisyphus 的 agent 写测试时调这两个 skill，**repo 必须提供**：

```
.claude/skills/
├── unit-test/SKILL.md        # 单测约定（pure vs service-dependent 等）
└── integration-test/SKILL.md # 集成测试约定（docker-compose 起 stack / env BASE_URL）
```

详见 `ttpos-server-go/.claude/skills/unit-test/` 和 `ubox-crosser/.claude/skills/integration-test/` 作为参考实现。

---

## 6. `.github/workflows/ci.yml` 要求

调 `phona/ttpos-ci` 的 reusable workflow（模板），包含：

- `lint` / `unit-test` / `integration-test` / `sonarqube` 四个 job
- **必须**有 `image-publish` job，依赖前 4 个 job 全 success：
  - build + push image 到 `ghcr.io/<owner>/<repo>:<REQ>-sha-<short>`
  - 用 `github.rest.repos.createCommitStatus` 把 image_tag 写到 `CI / image-publish` status 的 `description` 字段
- 触发方式：push 到 `stage/REQ-*` 分支自动触发 CI（**sisyphus 不显式触发**，靠 GHA 自己的触发器）

缺 image-publish job → PR CI 永远拿不到 image_tag → sisyphus pr-ci-watch 超时 escalate。

---

## 7. `tests/docker-compose.yml` 要求

`ci-integration-test` 用这个起 stack。约定：

- `test-runner` 容器作为 go test 执行方
- service container 以 env 方式注入地址给 test-runner：`BASE_URL=http://service:8080`
- `docker compose up --build --exit-code-from test-runner` 能完整跑完

---

## 8. Sisyphus 给 repo 的承诺

| 承诺 | 说明 |
|---|---|
| 持久 workspace | PVC 生命周期绑 REQ，直到 done/escalate+7d 才清 |
| 隔离 runner | 每 REQ 独立 Pod + PVC + K8s namespace |
| 自动 secrets 注入 | 只读 GH_TOKEN / GHCR 凭证 / kubeconfig 自动挂进来 |
| 失败恢复 | K8s restartPolicy=Always；Pod 重启 workspace 不丢 |
| pause/resume | 资源紧张时可临时 suspend REQ（PVC 留），晚些 resume |

Sisyphus **不做**的事（repo 自己管）：
- 写 Makefile target 实现细节
- 写 docker-compose / helm chart
- 管 repo 内代码结构
- 决定 test 该怎么写（由 `.claude/skills/` 规范）

### 推送 / PR / Merge 不发生在 runner Pod

runner 是**调试环境**，只验证，不改东西。所有写操作（git push / gh pr create /
gh pr merge / gh issue create / openspec apply+push）都在 **BKD agent 自己的
Coder workspace cwd** 里跑，用 Coder 自带的可写 GH token。

runner Pod 拿的 `GH_TOKEN` secret 是**只读 PAT**，够 clone + pull + gh api 看
commit statuses，不能 push。这是安全分层，runner 失陷不会污染 upstream。

---

## 9. 新 repo onboarding 最短路径

最小可跑 sisyphus 的废 repo：

```
my-repo/
├── .claude/skills/
│   ├── unit-test/SKILL.md          # 从现有 repo 拷
│   └── integration-test/SKILL.md   # 从现有 repo 拷
├── .github/workflows/ci.yml        # 调 phona/ttpos-ci/ci-go.yml
├── Makefile                        # 下方 6 个 target
└── tests/docker-compose.yml        # 最简单的 echo service 就行
```

Makefile 最小实现（`ci-accept-env-*` 没 lab 就省）：

```makefile
ci-lint:
	@echo "skip lint"; true

ci-unit-test:
	go test ./...

ci-integration-test:
	docker compose -f tests/docker-compose.yml up --build --exit-code-from test-runner

ci-build:
	docker build -t $(SISYPHUS_IMAGE_TAG) .
```

5 分钟接入一个 repo。

---

## 10. 调试 / 问题排查

| 问题 | 怎么查 |
|---|---|
| REQ 卡住 | `curl /admin/req/REQ-N` 看 state + history + ctx.manifest_snapshot |
| manifest 格式错 | `kubectl exec runner-<req> -- validate-manifest.py` |
| workspace 丢 / 容器异常 | `POST /admin/req/REQ-N/rebuild-workspace`（S5 提供）|
| 资源紧想让路 | `POST /admin/req/REQ-N/pause`（S5 提供）|
| bug 积累分析 | `gh issue list --label sisyphus:pre-release-bug` / `ci-bug` / `post-release-bug` |
