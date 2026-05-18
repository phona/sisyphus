# Sisyphus 部署手册

> 从零起一套 sisyphus 控制面。覆盖 issue #574 中的 #1（集群清单）和 #2（secret/vars 全表）；
> #3 跨仓依赖图 / #4 运维 cheatsheet 后续补。
>
> 相关权威：
> - [orchestrator/deploy/README.md](../orchestrator/deploy/README.md) — 单次 deploy 操作步骤（aissh 路径）
> - [docs/integration-contracts.md §10](./integration-contracts.md#10-required-tokens--secrets--vars) — token 权限语义 / 缺失症状
> - [docs/golden-cow.md](./golden-cow.md) — golden_cow 模块和 RBAC
> - [docs/architecture.md](./architecture.md) — 组件分工

## 1. 从零起新集群清单

按顺序过；每步给出"完成判据"。

### 1.1 节点和基础组件

| # | 步骤 | 完成判据 |
|---|---|---|
| 1 | K3s ≥ v1.28 已起；`kubectl get nodes` 全 Ready | 至少 1 worker；privileged + DinD 支持 |
| 2 | Traefik ingress（K3s 默认带）可用 | `kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik` Ready |
| 3 | Longhorn 已装（accept ns 走 VolumeSnapshot 必需） | `kubectl -n longhorn-system get pod` 全 Ready；`kubectl get storageclass` 含 `longhorn` |
| 4 | snapshot CRD 在（Longhorn 1.5+ 自带 / 否则装 external-snapshotter） | `kubectl api-resources \| grep volumesnapshot` 列出 VolumeSnapshot / VolumeSnapshotClass / VolumeSnapshotContent |
| 5 | DNS：选好 ingress host（如 `sisyphus.<ip>.nip.io` / `metabase.<ip>.nip.io`） | curl 该域名 80 端口能落到 Traefik |

### 1.2 数据面

| # | 步骤 | 命令 / 文件 | 完成判据 |
|---|---|---|---|
| 6 | 装 Postgres（Bitnami chart） | `helm -n sisyphus install sisyphus bitnami/postgresql -f values/postgresql.yaml` | `sisyphus-postgresql-0` Running；`sisyphus` 库 + `sisyphus_obs` 库都建好 |
| 7 | 执行 schema 初始化 | initdb 自动跑首次；后续升级 `kubectl exec -it sisyphus-postgresql-0 -- psql -U sisyphus -f /tmp/schema.sql` 手动 | `\dt` 列出 `req_state` / `event_log` / `stage_runs` / `verifier_decisions` / `artifact_checks` |
| 8 | 跑 migrations | `for f in orchestrator/migrations/*.sql; do kubectl exec ... -- psql ... -f $f; done` | 0001 → 0009 全过 |
| 9 | （可选）装 Metabase | `helm -n sisyphus install sisyphus-bi pmint/metabase -f values/metabase.yaml` | Metabase Web 起来；连上 sisyphus PG；导入 [observability/queries/sisyphus/](../observability/queries/sisyphus/) Q1–Q22 |

### 1.3 控制面（orchestrator + runner）

| # | 步骤 | 文件 / 命令 | 完成判据 |
|---|---|---|---|
| 10 | 准备 secret 值 | `cp orchestrator/deploy/secrets.env.template secrets.env` 填全 5 项（见 §2.1） | 文件填全；**不要 git add** |
| 11 | 拼 secret values | 见 [orchestrator/deploy/README.md §3](../orchestrator/deploy/README.md) 生成 `/tmp/secrets-values.yaml` | 临时文件 600，部署后立即 `rm` |
| 12 | helm install orchestrator | `helm -n sisyphus upgrade --install orch ./orchestrator/helm -f orchestrator/deploy/my-values.yaml -f /tmp/secrets-values.yaml --set image.tag=sha-<short> --set runner.image=ghcr.io/phona/sisyphus-runner:sha-<short>` | `orch-sisyphus-orchestrator` Pod Ready；`curl http://<host>/healthz` 200 |
| 13 | RBAC 校验（如装了 golden_cow） | 检查 [orchestrator/helm/templates/golden-cow-rbac.yaml](../orchestrator/helm/templates/golden-cow-rbac.yaml) 已 apply | orch SA 有 VolumeSnapshot / Service / Secret cluster-scope perm（[golden-cow.md §8](./golden-cow.md)） |
| 14 | runner ns + RBAC + secret | `my-values.yaml` 里 `runner.createNamespace=true` 一并装 | `sisyphus-runners` ns 存在；`sisyphus-runner-secrets` Secret 含 5 个 key（§2.2） |

### 1.4 golden_cow / baseline lab（可选，只有走 ttpos 加速 accept 路才需要）

| # | 步骤 | 完成判据 |
|---|---|---|
| 15 | 起 `ttpos-arch-lab` baseline ns（nacos / rocketmq nameserver+broker） | baseline pod Ready；ClusterIP 可达 |
| 16 | 创 baseline `golden-volumes` ns + 物化首个 VolumeSnapshot | `kubectl -n golden-volumes get vs -l golden=true` 至少一条 |
| 17 | 写 golden_cow spec：`/etc/sisyphus/golden-cow.yaml`（或 mount 进 orch Pod） | 参考 [orchestrator/config/golden-cow.example.yaml](../orchestrator/config/golden-cow.example.yaml) |
| 18 | 建 `sisyphus/golden-credentials` secret（auth scenario REQ 必需） | [golden-cow.md §4.1.2](./golden-cow.md) 命令；optional=true 不建也能跑 noAuth |

### 1.5 BKD 接通

| # | 步骤 | 完成判据 |
|---|---|---|
| 19 | BKD launcher 已起；拿到 `Coder-Session-Token` 填进 `SISYPHUS_BKD_TOKEN` | `python3 scripts/bkd-cli.py list` 不报 401 |
| 20 | BKD 那边配 webhook：URL `http://<host>/bkd-events`，events `issue.updated` / `session.completed` / `session.failed`，secret 用 `SISYPHUS_WEBHOOK_TOKEN` | 派一条 dogfood REQ，orch 日志能收到 `webhook.received` |

### 1.6 跨仓 CI 链（接业务仓时）

| # | 步骤 | 完成判据 |
|---|---|---|
| 21 | 业务仓 `BUILD_REPO` (var) + `BUILD_REPO_PAT` (secret) 已配 | dispatch.yml 跑得起；ttpos-ci 收到 `repository_dispatch` |
| 22 | 业务仓 PR 上有至少一条 check-run | sisyphus pr_ci_watch 不会 1800s timeout |
| 23 | 用 §10.3 / [integration-contracts.md §10.3](./integration-contracts.md#103-接入新业务仓检查清单) 跑完 onboard checklist | dogfood REQ 端到端跑过 |

---

## 2. Secret / Vars 全表

### 2.1 sisyphus 侧 — `secrets.env`（首次部署填，部署方持有）

模板：[orchestrator/deploy/secrets.env.template](../orchestrator/deploy/secrets.env.template)

| Key | 用途 | 谁创建 | 怎么更新 | 缺失症状 |
|---|---|---|---|---|
| `SISYPHUS_BKD_TOKEN` | BKD REST API 鉴权（Coder-Session-Token） | BKD 管理员颁发 | 重新登 BKD → 复制 cookie；`kubectl patch secret orch-sisyphus-orchestrator` | orch 起不来；所有 stage agent spawn 401 |
| `SISYPHUS_WEBHOOK_TOKEN` | BKD → orch 入站 webhook 鉴权 | 部署方自生（留空脚本 `openssl rand -hex 32`） | 改完同时改 BKD webhook secret | BKD 推事件 401；REQ 永久卡当前 stage |
| `SISYPHUS_GH_TOKEN` | runner `git clone` 私有仓；orch 读 commit status | repo owner / 机器账号 | GitHub Settings → Fine-grained PAT 重签；`kubectl -n sisyphus-runners patch secret sisyphus-runner-secrets`（小写 `gh_token` key，[integration-contracts §10.3.1](./integration-contracts.md#1031-模式切换-no-intent--orch-led-source-repo-tag)） | clone 403/404；GHA pr-ci-watch 拿不到 status |
| `SISYPHUS_GHCR_USER` | `docker login ghcr.io` 用户名 | 机器账号 owner | 改账号时一起 | ImagePullBackOff |
| `SISYPHUS_GHCR_TOKEN` | `docker login ghcr.io` 密码 | classic PAT `read:packages` | 重签 PAT；同步 patch secret | ImagePullBackOff |
| `SISYPHUS_KUBECONFIG_PATH` | 本地 kubeconfig 路径（vm-node04 上） | 部署方 | 改集群时换路径 | runner accept-env-up 报 "Unable to connect to the server" |

### 2.2 K8s Secret 实际 layout（部署后的样子）

#### `sisyphus/orch-sisyphus-orchestrator`

| Secret key | 注入 env | 由什么填 |
|---|---|---|
| `bkd_token` | `SISYPHUS_BKD_TOKEN` | secrets.env |
| `webhook_token` | `SISYPHUS_WEBHOOK_TOKEN` | secrets.env |
| `pg_dsn` | `SISYPHUS_PG_DSN` | helm chart 模板从 `postgres.passwordSecret` 拼 |

#### `sisyphus-runners/sisyphus-runner-secrets`

| Secret key | 注入 env | 由什么填 |
|---|---|---|
| `gh_token` | `GH_TOKEN` | secrets.env / Fine-grained PAT |
| `ghcr_user` | `SISYPHUS_GHCR_USER` | secrets.env |
| `ghcr_token` | `SISYPHUS_GHCR_TOKEN` | secrets.env / classic PAT |
| `kubeconfig`（文件挂载） | `KUBECONFIG=/workspace/.kubeconfig` | secrets.env 路径读入；entrypoint 重写 server 为 in-cluster |

#### `sisyphus/golden-credentials`（auth scenario 才用）

[golden-cow.md §4.1](./golden-cow.md#41-测试账号目录-golden-credentials-secret) — per-role JSON value（cashier/kiosk/tablet/admin），跟 golden VolumeSnapshot 同源。

### 2.3 业务仓侧 — GitHub repo Settings → Actions

| Name | 类型 | 用途 | 权限要求 | 缺失症状 |
|---|---|---|---|---|
| `BUILD_REPO_PAT` | Secret | dispatch.yml 触发 `BUILD_REPO` CI 仓的 `repository_dispatch` | PAT，目标 CI 仓 Actions: write（classic `repo` 或 fine-grained `Actions: read-and-write`） | dispatch 401；pr-ci-watch 无 check-run 永久 timeout |
| `BUILD_REPO` | Variable | dispatch.yml 中目标 CI 仓名 | 字符串如 `ZonEaseTech/ttpos-ci` | dispatch workflow 起不来 |
| `SOURCE_REPO_PAT`（CI 仓侧） | Secret | ttpos-ci workflow checkout source repo | 目标 source repo `Contents: read` | image-publish job checkout 403 |
| `DOCKER_USERNAME` / `DOCKER_PASSWORD`（CI 仓侧） | Secret | ttpos-ci push 镜像到 GHCR | GHCR 写权限的 PAT | image-publish 推不动 |

### 2.4 Orchestrator ConfigMap 可选 env

不配则功能静默降级，不影响核心状态机：

| Env var | 用途 | 不配时效果 |
|---|---|---|
| `SISYPHUS_GITHUB_TOKEN` | pr-ci-watch 轮 check-run、§6b CI dispatch、PR base drift 监控、escalation incident issue | dispatch/incident issue 跳过（warn log）；轮询可能 rate limit |
| `SISYPHUS_CI_DISPATCH_ENABLED` / `SISYPHUS_CI_DISPATCH_REPO` / `SISYPHUS_CI_DISPATCH_EVENT_TYPE` | 主动派 ttpos-ci 跑 PR CI（[integration-contracts §6b](./integration-contracts.md#6b-主动-dispatch可选ci_dispatch_enabledtrue)） | 不主动 dispatch，靠业务仓自家 GHA 自触 |
| `SISYPHUS_GOLDEN_COW_SPEC_PATH` | golden_cow spec 文件路径（默认 `/etc/sisyphus/golden-cow.yaml`） | 文件不存在 → setup 跳过，accept 走旧流程 |
| `SISYPHUS_ENABLED_PROMPT_HOOKS` | 全 stage 可启用的 prompt hook 列表（list[str]） | 默认 hook 集合 |
| `SISYPHUS_STAGE_PRECHECK_ENABLED` | per-stage 关 `ci-precheck`（JSON dict） | 全 stage 开 |

### 2.5 涉及方速查

| 资源 | 谁创建 | 谁更新 | 在哪 |
|---|---|---|---|
| `orch-sisyphus-orchestrator` secret | 部署方（首次） | 部署方 / 自动化 deploy.yml | `sisyphus` ns |
| `sisyphus-runner-secrets` | 部署方（首次） | 部署方按需 patch（业务仓 onboard 时扩 PAT scope） | `sisyphus-runners` ns |
| `golden-credentials` | snapshot owner（sisyphus golden_cow） | 跟 golden VolumeSnapshot 同步换 | `sisyphus` ns |
| `BUILD_REPO_PAT` / `BUILD_REPO` | 业务仓 owner | 业务仓 owner | 业务仓 Settings |
| `SOURCE_REPO_PAT` / `DOCKER_*` | ttpos-ci 仓 owner | ttpos-ci 仓 owner | ttpos-ci 仓 Settings |
| `KUBECONFIG`（GHA 自动 deploy 用） | 部署方 | 集群换 cert 时 | sisyphus repo Settings |

---

## 3 / 4. 跨仓依赖图 / 运维 cheatsheet（TODO）

issue #574 列的 #3 和 #4 后续补，触发点：

- 跨仓依赖图（mermaid: ttpos-server-go → ttpos-ci → ghcr → orch → BKD / k8s）：撞一次跨仓 root cause 排查 ≥ 30min 时立题补。
- 运维 cheatsheet（stale runner pod / accept ns / BKD issue 清理 / admin endpoint 用法 / escalate 排查）：收集 #561 / #572 等 issue 评论里散落命令，攒到 ≥ 5 条再沉淀。
