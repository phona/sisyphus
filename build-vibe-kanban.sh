#!/bin/bash
# Vibe Kanban 本地构建脚本
# 从源码构建并加载到 kind 集群

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="vibe-kanban"
IMAGE_TAG="local"
KIND_CLUSTER="kind"

echo "========================================"
echo "  🔨 Vibe Kanban 本地构建"
echo "========================================"
echo ""

# 检查前置依赖
command -v git >/dev/null 2>&1 || { echo "❌ 需要 git"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "❌ 需要 docker"; exit 1; }
command -v kind >/dev/null 2>&1 || { echo "❌ 需要 kind"; exit 1; }

# 克隆源码
if [ ! -d "/tmp/vibe-kanban" ]; then
    echo "📥 克隆 Vibe Kanban 源码..."
    git clone https://github.com/BloopAI/vibe-kanban.git /tmp/vibe-kanban --depth 1
else
    echo "📁 使用已有源码 /tmp/vibe-kanban"
fi

cd /tmp/vibe-kanban

# 检查是否有 Dockerfile
if [ -f "Dockerfile" ]; then
    echo "🐳 使用 Dockerfile 构建..."
    docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .
elif [ -f "local-build.sh" ]; then
    echo "🔧 使用 local-build.sh 构建..."
    chmod +x local-build.sh
    ./local-build.sh
    # 构建完成后需要打标签
    docker tag vibe-kanban:latest ${IMAGE_NAME}:${IMAGE_TAG} 2>/dev/null || true
else
    echo "⚠️  没有标准构建脚本，尝试通用方案..."
    
    # 检查项目结构
    if [ -f "Cargo.toml" ] && [ -f "package.json" ]; then
        echo "检测到 Rust + Node 项目"
        echo "创建简单 Dockerfile..."
        
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
        docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .
    else
        echo "❌ 无法识别项目结构，请手动构建"
        exit 1
    fi
fi

echo ""
echo "📤 加载镜像到 kind 集群..."
kind load docker-image ${IMAGE_NAME}:${IMAGE_TAG} --name ${KIND_CLUSTER}

echo ""
echo "🚀 部署到集群..."
# 更新 values 使用本地镜像
cat > /tmp/vibe-kanban-local-values.yaml << EOF
image:
  repository: ${IMAGE_NAME}
  tag: ${IMAGE_TAG}
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 3000
  backendPort: 3001

database:
  external:
    enabled: true
    host: postgres-shared-postgresql.sisyphus.svc.cluster.local
    port: 5432
    database: vibekanban
    username: postgres
    password: devops

env:
  PORT: "3000"
  BACKEND_PORT: "3001"
  HOST: "0.0.0.0"
  MCP_HOST: "0.0.0.0"
  VK_ALLOWED_ORIGINS: "http://vibe-kanban.sisyphus.svc.cluster.local:3000,http://localhost:3000"
  POSTHOG_API_KEY: ""
  POSTHOG_API_ENDPOINT: ""

resources:
  requests:
    cpu: 200m
    memory: 512Mi
  limits:
    cpu: 1000m
    memory: 2Gi

persistence:
  enabled: true
  size: 5Gi
  accessMode: ReadWriteOnce
EOF

helm upgrade --install vibe-kanban ${SCRIPT_DIR}/charts/vibe-kanban \
    --namespace sisyphus \
    --values /tmp/vibe-kanban-local-values.yaml \
    --wait --timeout 5m

echo ""
echo "========================================"
echo "  ✅ Vibe Kanban 部署完成！"
echo "========================================"
echo ""
echo "访问地址:"
echo "  http://vibe-kanban.sisyphus.svc.cluster.local:3000"
echo ""
echo "查看日志:"
echo "  kubectl logs -n sisyphus deployment/vibe-kanban"
