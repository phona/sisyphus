# Vibe Kanban 手动构建指南

由于 Vibe Kanban 镜像未发布到 Docker Hub，需要本地构建。

## 🏗️ 构建步骤

### 1. 克隆源码

```bash
# 在有网络的环境中克隆
git clone https://github.com/BloopAI/vibe-kanban.git /tmp/vibe-kanban
cd /tmp/vibe-kanban
```

### 2. 检查构建方式

```bash
# 查看是否有 Dockerfile
ls Dockerfile 2>/dev/null || echo "无 Dockerfile"

# 查看 local-build.sh
cat local-build.sh
```

### 3. 执行构建

**方式 A：使用官方构建脚本（推荐）**
```bash
cd /tmp/vibe-kanban
chmod +x local-build.sh
./local-build.sh

# 检查生成的镜像
docker images | grep vibe
```

**方式 B：手动 Docker 构建**
```bash
cd /tmp/vibe-kanban

# 如果有 Dockerfile
docker build -t vibe-kanban:local .

# 如果没有 Dockerfile，创建多阶段构建
cat > Dockerfile << 'EOF'
FROM node:20-alpine AS frontend
WORKDIR /app
COPY packages/local-web/package*.json ./
RUN npm install
COPY packages/local-web/ ./
RUN npm run build

FROM rust:1.75-alpine AS backend
RUN apk add --no-cache musl-dev openssl-dev
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
RUN cargo build --release

FROM alpine:latest
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=frontend /app/dist ./web
COPY --from=backend /app/target/release/vibe-kanban ./
EXPOSE 3000 3001
CMD ["./vibe-kanban"]
EOF

docker build -t vibe-kanban:local .
```

### 4. 加载到 kind

```bash
# 确保镜像存在
docker images | grep vibe-kanban

# 加载到 kind 集群
kind load docker-image vibe-kanban:local --name kind
```

### 5. 部署到集群

```bash
cd ~/Desktop/Projects/sisyphus

# 方式 1：使用 Makefile
make deploy-vibe-kanban-local

# 方式 2：使用 Helm 直接部署
helm upgrade --install vibe-kanban charts/vibe-kanban \
  --namespace sisyphus \
  --values values/vibe-kanban-local.yaml \
  --set image.repository=vibe-kanban \
  --set image.tag=local \
  --set image.pullPolicy=IfNotPresent \
  --wait
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

### 构建失败

```bash
# 检查依赖
rustc --version      # 需要 Rust 1.75+
node --version       # 需要 Node 20+
pnpm --version       # 需要 pnpm 8+

# 安装构建工具
cargo install cargo-watch
cargo install sqlx-cli
```

### 镜像加载失败

```bash
# 检查 kind 集群
kind get clusters

# 重新加载
docker save vibe-kanban:local | kind load image-archive - --name kind
```

### 数据库连接失败

```bash
# 确保数据库已创建
kubectl exec postgres-shared-postgresql-0 -n sisyphus -- \
  bash -c 'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE vibekanban;"'
```

## 📋 快速命令

在项目根目录执行：

```bash
# 一键构建并部署（本地执行）
make build-vibe-kanban-local

# 仅部署（镜像已存在）
make deploy-vibe-kanban-local

# 查看状态
make status

# 查看日志
kubectl logs -n sisyphus deployment/vibe-kanban -f
```
