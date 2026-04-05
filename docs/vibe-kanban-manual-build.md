# Vibe Kanban 构建指南

预先构建包含 vibe-kanban 的 Docker 镜像，避免每次启动 Pod 时下载。

## 🏗️ 构建步骤

### 1. 一键构建并部署

```bash
cd ~/Desktop/Projects/sisyphus
make build-vibe-kanban-local
```

### 2. 手动构建

```bash
cd ~/Desktop/Projects/sisyphus

# 构建镜像（预装 vibe-kanban）
cd projects/vibe-kanban
docker build -t vibe-kanban:local .

# 加载到 kind
kind load docker-image vibe-kanban:local --name kind

# 部署
cd ../..
helm upgrade --install vibe-kanban charts/vibe-kanban \
  --namespace sisyphus \
  --values values/vibe-kanban-local.yaml \
  --wait
```

## 🐳 Dockerfile 说明

```dockerfile
FROM node:20-alpine
WORKDIR /app

# 构建时预装 vibe-kanban（不是启动时下载）
RUN npm install -g vibe-kanban

# 创建数据目录
RUN mkdir -p /data

EXPOSE 3000 3001

# 启动时直接使用预装的包
CMD ["npx", "vibe-kanban"]
```

## ✅ 验证部署

```bash
# 检查 Pod 状态
kubectl get pods -n sisyphus | grep vibe

# 查看日志
kubectl logs -n sisyphus deployment/vibe-kanban --tail=50

# 通过 telepresence 访问
telepresence connect
curl http://vibe-kanban.sisyphus.svc.cluster.local:3000
```

## 🔧 故障排除

### 数据库连接失败

```bash
# 确保数据库已创建
kubectl exec postgres-shared-postgresql-0 -n sisyphus -- \
  bash -c 'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE vibekanban;"'
```

## 📋 快速命令

```bash
# 构建并部署（推荐）
make build-vibe-kanban-local

# 仅部署（镜像已存在）
make deploy-vibe-kanban-local

# 查看日志
make logs-vibe-kanban
```
