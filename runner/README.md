# sisyphus-runner

每 REQ 一个 docker container，BKD agent 通过 aissh 进 vm-node04 后 `docker exec` 干活。

## 镜像内容

- Flutter SDK + Android SDK + Java（来自 `cirruslabs/flutter:stable`）
- Go 1.22
- Node + npm
- Docker DinD（contract test 跑 docker compose 用）
- openspec CLI
- sisyphus 合约脚本（`/opt/sisyphus/scripts/`）
- git / gh / curl / jq / make

## 用法（agent prompt 段）

```bash
# 在 vm-node04 上 (aissh exec_run)
CONTAINER=sisyphus-runner-$REQ
docker inspect $CONTAINER >/dev/null 2>&1 || docker run -d --name $CONTAINER \
  --privileged --restart=unless-stopped \
  -v sisyphus-workspace-$REQ:/workspace -w /workspace \
  ghcr.io/phona/sisyphus-runner:v1 sleep infinity

docker exec $CONTAINER bash -c "openspec validate openspec/changes/$REQ"
docker exec $CONTAINER bash -c "go test ./..."
docker exec $CONTAINER bash -c "docker compose -f tests/contract/docker-compose.yml up -d"
```

## 生命周期

- **创建**：第一个 stage（spec / dev）按需 idempotent 拉起
- **复用**：所有 pre-accept stage 都用同一个 container（cache 跨 stage 留存）
- **销毁**：sisyphus `done_archive` action 末尾删 container + volume；vm-node04 cron 兜底（state=done >7d 强删）

## 不在 runner 里跑的

- accept stage：用 ttpos-arch-lab K3s 部署而非 docker container
- BKD coder workspace：BKD 自己控制，sisyphus 不动它

## 两个 flavor

| flavor | Dockerfile | image | 大小 | 用途 |
|---|---|---|---|---|
| `full` | `runner/Dockerfile` | `ghcr.io/phona/sisyphus-runner` | ~5GB | Flutter / mobile 项目（带 Android SDK + Java） |
| `go` | `runner/go.Dockerfile` | `ghcr.io/phona/sisyphus-runner-go` | ~1GB | Go 项目（如 ubox-crosser），不带 Flutter |

prompt `runner_container.md.j2` 默认用 `:go`。Flutter 项目要切成 `:main`（full flavor）。

## 构建

`.github/workflows/runner-image.yml` matrix build 两个 flavor，push 到 GHCR：
- `:main` / `:sha-<short>`
- 打 tag `runner-v1.2.3` → `:1.2.3` / `:1.2` / `:1` / `:latest`
