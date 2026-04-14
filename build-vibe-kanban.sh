#!/bin/bash
# Vibe Kanban 本地构建脚本
# 预先构建包含 vibe-kanban 的镜像，避免每次启动时下载

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="vibe-kanban"
IMAGE_TAG="local"
KIND_CLUSTER="kind"

echo "========================================"
echo "  🔨 Vibe Kanban 镜像构建"
echo "========================================"
echo ""

# 检查前置依赖
command -v docker >/dev/null 2>&1 || { echo "❌ 需要 docker"; exit 1; }
command -v kind >/dev/null 2>&1 || { echo "❌ 需要 kind"; exit 1; }

# 构建镜像（预先安装 vibe-kanban）
echo "🐳 构建镜像（预装 vibe-kanban）..."
cd "${SCRIPT_DIR}/projects/vibe-kanban"
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .

echo ""
echo "📤 加载镜像到 kind 集群..."
kind load docker-image ${IMAGE_NAME}:${IMAGE_TAG} --name ${KIND_CLUSTER}

echo ""
echo "🚀 部署到集群..."
cat > /tmp/vibe-kanban-local-values.yaml << EOF
image:
  repository: ${IMAGE_NAME}
  tag: ${IMAGE_TAG}
  pullPolicy: IfNotPresent

# 不使用 command/args，用镜像默认的 CMD
command: []
args: []

# 禁用 MCP Proxy（我们使用 HTTP API 直接调用）
mcpProxy:
  enabled: false

service:
  type: ClusterIP
  port: 3000
  backendPort: 3001

# 使用共享 PostgreSQL
database:
  external:
    enabled: true
    host: postgres-shared-postgresql.sisyphus.svc.cluster.local
    port: 5432
    database: vibekanban
    username: postgres
    password: devops

# 环境变量配置
env:
  PORT: "3000"
  BACKEND_PORT: "3001"
  HOST: "0.0.0.0"
  MCP_HOST: "0.0.0.0"
  VK_ALLOWED_ORIGINS: "http://vibe-kanban.sisyphus.svc.cluster.local:3000,http://localhost:3000,http://127.0.0.1:3000"
  POSTHOG_API_KEY: ""
  POSTHOG_API_ENDPOINT: ""

# 额外环境变量（包括 Claude API Key）
extraEnv:
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: claude-api-key
        key: api-key
  - name: ANTHROPIC_MODEL
    value: "qwen3.6-plus"
  - name: ANTHROPIC_BASE_URL
    value: "https://coding.dashscope.aliyuncs.com/apps/anthropic"

# 资源限制
resources:
  requests:
    cpu: 200m
    memory: 512Mi
  limits:
    cpu: 1000m
    memory: 2Gi

# 持久化
persistence:
  enabled: true
  size: 5Gi
  accessMode: ReadWriteOnce
  mountPath: /data

# 项目配置 - 克隆 Git 仓库
projects:
  enabled: true
  storage:
    enabled: true
    size: 10Gi
  repos:
    - name: ttpos-server-go
      url: http://gitea-http.sisyphus.svc.cluster.local:3000/gitea_admin/ttpos-server-go.git
      branch: master
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
