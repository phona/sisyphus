# orchestrator 部署踩坑记

首次上 vm-node04 K3s 时栽过的坑。每条带"症状 → 根因 → 修复 commit"，下次回头查不重复掉。

---

## 1. uv pip install 不识别 `UV_PROJECT_ENVIRONMENT`

**症状**：Dockerfile build 在 `uv pip install` 那一层挂：
```
error: No virtual environment found; run `uv venv` to create an environment,
or pass `--system` to install into a non-virtual environment
```

**根因**：`UV_PROJECT_ENVIRONMENT` 只对 `uv sync` 生效，`uv pip install` 不读。

**修复**：用标准 `VIRTUAL_ENV` + `PATH`：
```dockerfile
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
RUN uv venv $VIRTUAL_ENV && uv pip install ...
```

commit `0598513`。

---

## 2. wheel 安装下 migrations 目录解析错位

**症状**：容器启动日志：
```
{"path": "/opt/venv/lib/python3.12/migrations", "event": "migrate.no_dir", "level": "warning"}
```
然后 webhook 进来 `req_state` 表不存在直接 500。

**根因**：`migrate.py` 用 `Path(__file__).resolve().parents[2]` 找 migrations。
- dev editable 安装：`src/orchestrator/migrate.py` → parents[2] = repo 根 ✓
- wheel 安装：`site-packages/orchestrator/migrate.py` → parents[2] = `python3.12/` ✗

**修复**：环境变量优先 + cwd 兜底：
```python
env > Path.cwd()/migrations > parents[2]/migrations
```
Dockerfile 设 `SISYPHUS_MIGRATIONS_DIR=/app/migrations` 兜住容器。

commit `b8a47f6`。

---

## 3. yoyo 不认 `postgresql+psycopg2://` scheme

**症状**：
```
yoyo.connections.BadConnectionURI: Unrecognised database connection scheme 'postgresql+psycopg2'
```

**根因**：我自己脑补的 scheme 名。yoyo 注册的 entry point 是 `postgresql`（默认就走 psycopg2 driver，psycopg2-binary 装了就用），不接 `+psycopg2` 后缀。

**修复**：删掉 DSN 改写函数，asyncpg 和 yoyo 共用 `postgresql://` 一份。

commit `bf5e8c2`。

---

## 4. yoyo SQL migration **不支持** 内联 `-- !rollback` 段（关键坑）

**症状**：
- 手工 `psql -f 0001_init.sql` 后 `\dt` 全空
- 让 yoyo 自己 apply 也一样 —— 日志看 "applying step 0...6" 一共 7 步，DB 却没表
- snapshot loop 报 `relation "req_state" does not exist`

**根因**：yoyo 的 SQL migration 解析器**不识别** `-- !rollback` 作为分段标记。
它把整个 .sql 文件当 forward 一把跑 —— 含我写的 DROP 段。所以 yoyo apply 真把表
建了又立刻 drop 了，但 yoyo 仍记录"apply 成功"，下次启动就以为已 apply 跳过，
表永远不会回来。

**正确做法**：rollback 必须放**单独文件** `<id>.rollback.sql`：
```
migrations/
  0001_init.sql            # 只 forward (CREATE TABLE...)
  0001_init.rollback.sql   # rollback (DROP TABLE...) — yoyo apply 不读这个
```

**踩了之后怎么救**（DB 状态卡住了）：
```bash
# 1. 清掉 yoyo 的"已 apply"假记录
PG_PASS=$(kubectl -n sisyphus get secret sisyphus-postgresql -o jsonpath='{.data.password}' | base64 -d)
kubectl -n sisyphus exec sisyphus-postgresql-0 -- env PGPASSWORD="$PG_PASS" \
  psql -U sisyphus -d sisyphus -c "DELETE FROM _yoyo_migration WHERE migration_id LIKE '0001%';"
# 2. 推一份正确格式的 0001_init.sql + 0001_init.rollback.sql
# 3. 重启 pod，yoyo 重新 apply forward → 表回来

# 真要紧急手工 apply（不走 yoyo），用 awk 只取 forward：
# （新格式不需要这一步，已无内联 rollback）
```

commit `da02198`。

**关联坑**：yoyo SQL 把文件按 `;` 切 step。末尾留纯注释（无后续语句）会触发
`psycopg2.ProgrammingError: can't execute an empty query`。SQL 文件最后一句必须以 `;` 结尾，
后面不留注释。

---

## 5. Helm `ternary` 不吃空字符串

**症状**：`helm lint` 报：
```
wrong type for value; expected bool; got string
```

**根因**：Sprig 的 `ternary` 第三参要 bool，不是 truthy/falsy。`.Values.existingSecret`（空字符串）会触发类型错误。

**修复**：用 `if/else` 替代：
```yaml
key: {{ if .Values.existingSecret }}{{ .Values.existingSecretKeys.bkd_token }}{{ else }}bkd_token{{ end }}
```

commit `de79294` 之前批次。

---

## 6. Helm 模板 `{{- /* */ -}}` 注释吃换行

**症状**：渲染出来 `annotations:checksum/config:`（冒号挨在一起），yaml 报 `mapping values are not allowed`。

**根因**：`{{- ... -}}` 双侧 trim，前后 whitespace 全没了。

**修复**：用普通 `# 注释`，不用 Go template 注释：
```yaml
annotations:
  # configmap/secret 改了自动滚动重启
  checksum/config: ...
```

commit `de79294` 之前批次。

---

## 7. GHCR package 默认 private（没踩到，但容易踩）

**预防**：首次 GHA push 后，`https://github.com/<owner>?tab=packages` 找到 `sisyphus-orchestrator`，
Settings → Change visibility → Public。

不改 K8s 拉镜像会 `ImagePullBackOff`，要么改 public，要么在 sisyphus ns 配 docker-registry secret + helm values 加 `imagePullSecrets`。

---

## 8. helm values_dev / kubeconfig / secrets 落地约定

避免泄密：
- `helm/values.local.yaml` / `helm/values.dev.yaml` 不入 git（`.gitignore` 已加 `helm/values.local.yaml`）
- 部署用临时文件 `/tmp/sisyphus-deploy/orchestrator/helm/values.local.yaml`，跑完不入 repo
- `webhook_token` 用 `openssl rand -hex 24` 生成，存到 `/tmp/sisyphus-deploy/webhook-token.txt` 或直接读 K8s Secret
- BKD `Coder-Session-Token` 是 user-level token，名下所有 project 共用

---

## 部署前 checklist（下次复用）

```
[ ] vm-node04 上 helm/kubectl 可用
[ ] storageClass=local-path（K3s 默认有）
[ ] 添加 bitnami helm repo
[ ] kubectl create ns sisyphus
[ ] helm install sisyphus-postgresql bitnami/postgresql -n sisyphus \
      --set auth.username=sisyphus,auth.database=sisyphus \
      --set primary.persistence.enabled=true,primary.persistence.size=20Gi
[ ] PG 起来后建 sisyphus_obs 库 + apply observability/schema.sql
[ ] git clone --depth 1 phona/sisyphus 到 vm-node04 /tmp 拿 helm chart
[ ] cp helm/values.dev.yaml my.yaml，填 secret.bkd_token + 生成 webhook_token
[ ] helm install orch ./helm -n sisyphus -f my.yaml --wait
[ ] 验 healthz：curl -H 'Host: sisyphus.coder.tbc.5ok.co' http://localhost/healthz
[ ] 验 401：curl -H 'Host: ...' -d '{}' http://localhost/bkd-events  # 应该 401
[ ] BKD 那边配 webhook，header 带 X-Sisyphus-Token
```
