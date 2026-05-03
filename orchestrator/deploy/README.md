# Sisyphus v0.2 部署交付

## 文件

| 文件 | 作用 | 有敏感信息 |
|---|---|---|
| `my-values.yaml` | helm values，指向 `existingSecret`；非敏感 | 否 |
| `deploy-secrets.sh` | 在 vm-node04 交互式建 secret（命令行输入不落 log）| **运行时用，不进 repo** |
| `README.md` | 本文件 | 否 |

## 操作顺序（用户 SSH 不到 vm-node04 的场景 — 上传文件走 aissh 部署）

### 步骤 1：准备两个 PAT（在 GitHub 上）

**PAT #1 — Fine-grained**（给 `gh_token`；给 runner 做 clone + commit status read）
- Resource owner: 机器账号或 ZonEaseTech（org 开 fine-grained 批复才可选）
- Repository access: 选 sisyphus 要接入的 repo 列表
- Permissions:
  - Contents → **Read-only**
  - Commit statuses → **Read-only**
  - Metadata → Read（自动）
- 生成 → 保存

**PAT #2 — Classic**（给 `ghcr_token`；GHCR 拉镜像）
- scope **只勾** `read:packages`
- 生成 → 保存

### 步骤 2：填 `secrets.env`

```bash
cp orchestrator/deploy/secrets.env.template secrets.env
# 编辑 secrets.env，填 5 个值
```

**不要** git add / commit（`orchestrator/deploy/.gitignore` 已拦）。

### 步骤 3：把 `secrets.env` 送到 vm-node04

用 croc（或其他 P2P 工具）把 `secrets.env` 发到 vm-node04 上的 `/tmp/`。
部署方（= 我）不处理 secret 内容；只从 env 文件拼出 **helm values override**，
让 Helm 自己建并管理 secret（K8s 标准所有权模式）：

```bash
source /tmp/secrets.env
export WH=${SISYPHUS_WEBHOOK_TOKEN:-$(openssl rand -hex 32)}

cat > /tmp/secrets-values.yaml <<EOF
secret:
  bkd_token: "$SISYPHUS_BKD_TOKEN"
  webhook_token: "$WH"
runner:
  secret:
    gh_token: "$SISYPHUS_GH_TOKEN"
    ghcr_user: "$SISYPHUS_GHCR_USER"
    ghcr_token: "$SISYPHUS_GHCR_TOKEN"
    kubeconfig: |
$(sed 's/^/      /' "${SISYPHUS_KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}")
EOF
chmod 600 /tmp/secrets-values.yaml
```

（不落聊天 transcript：values 文件写进 vm-node04 /tmp，用完 `rm` 清。）

### 步骤 4：`my-values.yaml` 确认（通常默认值就够）

如果要改 ingress host / 资源 limit 等非敏感配置：直接改 `my-values.yaml` 后
git commit + push。部署方拉最新版再跑 helm。

### 步骤 5：部署方 helm upgrade + verify

```bash
# 获取最近一次 CI build 的 orchestrator sha tag（或本地 git sha）
SHA="sha-$(git rev-parse --short HEAD)"

# 部署方通过 aissh 跑（两个 values file：非敏感 my-values + 敏感 secrets-values）
helm -n sisyphus upgrade --install orch ./orchestrator/helm \
  -f orchestrator/deploy/my-values.yaml \
  -f /tmp/secrets-values.yaml \
  --set image.tag="$SHA" \
  --set runner.image="ghcr.io/phona/sisyphus-runner:$SHA"
rm -f /tmp/secrets-values.yaml            # 敏感文件用完立即清

kubectl -n sisyphus rollout status deploy/orch-sisyphus-orchestrator
curl -sSH 'Authorization: Bearer <webhook_token>' \
  http://sisyphus.43.239.84.24.nip.io/admin/metrics | jq .state_distribution
```

> **image.tag / runner.image 必须 pin immutable sha**（issue #267）。
> 部署时忘传 `--set image.tag` 会让 helm template 报错
> `values.image.tag is required`，fail-loud 而不是渲染出损坏的 image ref。
> `:main` / `:latest` / `:dev` 等 mutable tag 已禁止——它们看不出对应哪个
> commit，且 `IfNotPresent` 时节点缓存的旧镜像永远不会被刷新。

### 步骤 6：BKD webhook 注册（一次性）

BKD 那边加 webhook：
- URL: `http://sisyphus.43.239.84.24.nip.io/bkd-events`（按 my-values.yaml 的 host 改）
- Events: `issue.updated`, `session.completed`, `session.failed`
- Secret: `secrets.env` 里的 `SISYPHUS_WEBHOOK_TOKEN`（若空自动生成的那个，部署输出里会打印）

### 步骤 4：BKD webhook 注册（一次性）

BKD 那边加 webhook：
- URL: `http://sisyphus.43.239.84.24.nip.io/bkd-events`（按 my-values.yaml 的 host 改）
- Events: `issue.updated`, `session.completed`, `session.failed`
- Secret: 填步骤 2 的 `webhook_token`（BKD 会自动包成 `Authorization: Bearer ...`）

## 什么都不进仓库的敏感项

- `.env*`（deploy-secrets.sh 里 `read -s` 输入后不落盘；默认也不存 `.env`）
- 临时 kubeconfig（`/tmp/sisyphus-kubeconfig.tmp`，脚本结尾 `rm -f` 清掉）
- PAT 本体（只进 K8s secret）

## 自动化部署（GitHub Actions）

`.github/workflows/deploy.yml` 在 `main` 分支的 `orchestrator-ci` 通过且 `image-publish` 完成后自动触发：

1. 计算镜像 tag：`sha-<short>`（与 `orchestrator-ci` 推送到 GHCR 的 tag 对齐）
2. `helm upgrade` 到 K8s 集群（`--reuse-values` 保留已有配置，仅更新镜像 tag）
3. 部署后通过 `kubectl exec` 在 Pod 内 curl `/healthz` 做健康检查
4. 健康检查失败自动 `helm rollback`

### 需要配置的 Secrets

在仓库 **Settings → Secrets and variables → Actions** 里添加：

| Secret | 内容 |
|---|---|
| `KUBECONFIG` | **base64 编码**的 kubeconfig（`cat ~/.kube/config \| base64 -w0`）。用于 GH Actions runner 连 K3s 集群。 |

### 手动触发

`workflow_dispatch` 支持：
- `image_tag`：指定镜像 tag（如 `sha-abc1234`、`main`）
- `release_name`：Helm release 名（默认 `orch`，与现有部署一致）
- `namespace`：K8s namespace（默认 `sisyphus`）

### 镜像构建链路

- `orchestrator-ci.yml` 在 `push` 到 `main` / tag 时构建并推送镜像到 GHCR（`:sha-<short>` / `:main` / semver tag）
- `deploy.yml` 复用已推送的镜像，不重复 build

## 回滚

1. Helm 回退：`helm -n sisyphus rollback orch`
2. 彻底清 v0.2 状态（谨慎）：
   ```bash
   # 清 v0.2 独有资源（保留 sisyphus ns 和 PG）
   kubectl delete ns sisyphus-runners   # 所有 runner pod+pvc 一起清
   kubectl -n sisyphus delete secret orch-sisyphus-orchestrator  # 如果想重来
   ```

## 故障排查

见 `orchestrator/docs/V0.2-PLAN.md` + `orchestrator/docs/RUNBOOK.md`。
