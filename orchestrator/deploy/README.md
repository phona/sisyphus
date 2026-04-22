# Sisyphus v0.2 部署交付

## 文件

| 文件 | 作用 | 有敏感信息 |
|---|---|---|
| `my-values.yaml` | helm values，指向 `existingSecret`；非敏感 | 否 |
| `deploy-secrets.sh` | 在 vm-node04 交互式建 secret（命令行输入不落 log）| **运行时用，不进 repo** |
| `README.md` | 本文件 | 否 |

## 操作顺序（给用户 = 你）

### 步骤 1：准备两个 PAT（在 GitHub 上）

**PAT #1 — Fine-grained**（给 `gh_token`；给 runner 做 clone + commit status read）
- Resource owner: 你的机器账号（个人）或 ZonEaseTech（如果 org 开了 fine-grained 批复）
- Repository access: 选 sisyphus 要接入的 repo 列表
- Permissions：
  - Contents → **Read-only**
  - Commit statuses → **Read-only**
  - Metadata → Read（自动）
- 生成 → 保存

**PAT #2 — Classic**（给 `ghcr_token`；GHCR 拉镜像）
- scope **只勾** `read:packages`
- 生成 → 保存

### 步骤 2：建 secret（在 vm-node04 上）

```bash
ssh vm-node04
cd <sisyphus repo>/orchestrator/deploy
bash deploy-secrets.sh
```

脚本会按顺序问：
1. BKD token（Coder-Session-Token，从 BKD 那边拿）
2. webhook_token（自家发的；回车自动 `openssl rand -hex 32` 生成；**记下来给 BKD webhook 配置用**）
3. GH Fine-grained PAT（PAT #1）
4. GHCR 用户名（机器账号 username）
5. GHCR Classic PAT（PAT #2）
6. kubeconfig 路径（默认 `/etc/rancher/k3s/k3s.yaml`）

跑完会 verify 两个 secret 都建好。

### 步骤 3：把 `my-values.yaml` 提交到 repo + 通知部署

```bash
git add orchestrator/deploy/my-values.yaml
git commit -m "deploy: sisyphus v0.2 values (non-secret)"
git push
```

然后告诉 sisyphus 部署方（我）"可以部署了"，我会通过 aissh 跑：

```bash
helm -n sisyphus upgrade --install orch ./orchestrator/helm -f deploy/my-values.yaml
kubectl -n sisyphus rollout status deploy/orch-sisyphus-orchestrator
```

然后验 `/admin/metrics` 回 200。

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
