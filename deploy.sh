#!/bin/bash
# Sisyphus DevOps - 一键部署脚本
# 使用本地 Helm Charts（避免网络超时）

set -e

NAMESPACE="sisyphus"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "  Sisyphus DevOps 一键部署"
echo "========================================"
echo ""

# 检查依赖
echo ">>> 检查依赖..."
command -v kubectl >/dev/null 2>&1 || { echo "❌ 需要 kubectl"; exit 1; }
command -v helm >/dev/null 2>&1 || { echo "❌ 需要 helm"; exit 1; }
echo "✅ 依赖检查通过"

# 检查集群
echo ">>> 检查集群..."
kubectl cluster-info || { echo "❌ 无法连接集群"; exit 1; }
echo "✅ 集群连接正常"

# 创建命名空间
echo ">>> 创建命名空间..."
kubectl create namespace $NAMESPACE 2>/dev/null || echo "命名空间已存在"

# 部署 PostgreSQL
echo ">>> 部署 PostgreSQL..."
helm upgrade --install postgres-shared "$SCRIPT_DIR/charts/postgresql" \
  --namespace $NAMESPACE \
  --values "$SCRIPT_DIR/values/postgres.yaml" \
  --wait --timeout 5m

# 等待 PostgreSQL 就绪
echo ">>> 等待 PostgreSQL 就绪..."
kubectl wait --for=condition=ready pod/postgres-shared-postgresql-0 -n $NAMESPACE --timeout=120s

echo "✅ PostgreSQL 就绪"

# 创建 Gitea admin secret
echo ">>> 创建 Gitea 管理员账号..."
kubectl create secret generic gitea-admin-secret \
  --namespace $NAMESPACE \
  --from-literal=username=gitea_admin \
  --from-literal=password=admin123 2>/dev/null || echo "Secret 已存在"

# 创建数据库
echo ">>> 创建数据库..."
kubectl exec -n $NAMESPACE postgres-shared-postgresql-0 -- bash -c \
  'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE gitea;"' 2>/dev/null || true
kubectl exec -n $NAMESPACE postgres-shared-postgresql-0 -- bash -c \
  'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE n8n;"' 2>/dev/null || true

echo "✅ 数据库创建完成"

# 部署 Gitea
echo ">>> 部署 Gitea..."
helm upgrade --install gitea "$SCRIPT_DIR/charts/gitea" \
  --namespace $NAMESPACE \
  --values "$SCRIPT_DIR/values/gitea.yaml" \
  --wait --timeout 5m

echo "✅ Gitea 就绪"

# 部署 n8n
echo ">>> 部署 n8n..."
helm upgrade --install n8n "$SCRIPT_DIR/charts/n8n" \
  --namespace $NAMESPACE \
  --values "$SCRIPT_DIR/values/n8n.yaml" \
  --wait --timeout 5m

echo "✅ n8n 就绪"

# 部署 Vibe Kanban
echo ">>> 部署 Vibe Kanban..."
kubectl exec -n $NAMESPACE postgres-shared-postgresql-0 -- bash -c \
  'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE vibekanban;"' 2>/dev/null || true
helm upgrade --install vibe-kanban "$SCRIPT_DIR/charts/vibe-kanban" \
  --namespace $NAMESPACE \
  --values "$SCRIPT_DIR/values/vibe-kanban.yaml" \
  --wait --timeout 5m

echo "✅ Vibe Kanban 就绪"

# 部署 Gitea Runner
echo ">>> 部署 Gitea Runner..."
kubectl apply -f "$SCRIPT_DIR/charts/gitea-act-runner.yaml"

echo "✅ Gitea Runner 就绪"

echo ""
echo "========================================"
echo "  ✅ 部署完成！"
echo "========================================"
echo ""
echo "服务状态:"
kubectl get pods -n $NAMESPACE
echo ""
echo "访问方式（使用 telepresence）:"
echo "  telepresence connect"
echo "  curl http://gitea-http.$NAMESPACE.svc.cluster.local:3000"
echo "  curl http://n8n.$NAMESPACE.svc.cluster.local:5678"
echo "  curl http://vibe-kanban.$NAMESPACE.svc.cluster.local:3000"
echo ""
echo "账号信息:"
echo "  Gitea: gitea_admin / admin123"
echo "  n8n:   admin / admin123"
