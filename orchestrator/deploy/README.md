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

### 步骤 3：把 `secrets.env` 上传给 sisyphus 部署方（= 我）

用聊天附件直接拖进来。部署方流程：
- `file_deploy` secrets.env → vm-node04 `/tmp/secrets.env`
- `exec_run` `bash deploy-secrets.sh --env-file /tmp/secrets.env`
- 脚本跑完自动 `shred -u` 清掉 `/tmp/secrets.env`（敏感文件不残留）
- content 全程不进聊天 transcript

### 步骤 4：`my-values.yaml` 确认（通常默认值就够）

如果要改 ingress host / 资源 limit 等非敏感配置：直接改 `my-values.yaml` 后
git commit + push。部署方拉最新版再跑 helm。

### 步骤 5：部署方 helm upgrade + verify

```bash
# 部署方通过 aissh 跑
helm -n sisyphus upgrade --install orch ./orchestrator/helm \
  -f orchestrator/deploy/my-values.yaml
kubectl -n sisyphus rollout status deploy/orch-sisyphus-orchestrator
curl -sSH 'Authorization: Bearer <webhook_token>' \
  http://sisyphus.43.239.84.24.nip.io/admin/metrics | jq .state_distribution
```

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
