#!/bin/bash
# Vibe Kanban 一键部署脚本（含自动代码推送）
# 用法: ./scripts/deploy-vibe-kanban.sh [namespace] [gitea_user] [gitea_pass]

set -e

NAMESPACE="${1:-sisyphus}"
GITEA_USER="${2:-gitea_admin}"
ITEA_PASS="${3:-admin123}"
GITEA_URL="http://gitea-http.${NAMESPACE}.svc.cluster.local:3000"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "========================================"
echo "  Vibe Kanban 部署脚本"
echo "========================================"
echo "  Namespace: $NAMESPACE"
echo "  Gitea: $GITEA_USER @ $GITEA_URL"
echo ""

# 1. 创建 namespace
echo "[1/6] 检查 namespace..."
if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
    echo "      创建 namespace: $NAMESPACE"
    kubectl create namespace "$NAMESPACE"
else
    echo "      Namespace 已存在"
fi

# 2. 确保基础服务已部署 (postgres, gitea)
echo "[2/6] 检查依赖服务..."
if ! kubectl get deployment postgres-shared-postgresql -n "$NAMESPACE" &>/dev/null; then
    echo "      部署 PostgreSQL..."
    helm upgrade --install postgres-shared "${PROJECT_ROOT}/charts/postgresql" \
        --namespace "$NAMESPACE" \
        --values "${PROJECT_ROOT}/values/postgres.yaml" \
        --wait
else
    echo "      PostgreSQL 已存在"
fi

if ! kubectl get deployment gitea -n "$NAMESPACE" &>/dev/null; then
    echo "      部署 Gitea..."
    kubectl create secret generic gitea-admin-secret \
        -n "$NAMESPACE" \
        --from-literal=username="$GITEA_USER" \
        --from-literal=password="$ITEA_PASS" 2>/dev/null || true
    
    helm upgrade --install gitea "${PROJECT_ROOT}/charts/gitea" \
        --namespace "$NAMESPACE" \
        --values "${PROJECT_ROOT}/values/gitea.yaml" \
        --wait
else
    echo "      Gitea 已存在"
fi

# 3. 创建或复用 Gitea token
echo "[3/6] 配置 Gitea 访问令牌..."
if ! kubectl get secret vibe-kanban-gitea -n "$NAMESPACE" &>/dev/null; then
    echo "      等待 Gitea 就绪..."
    for i in {1..30}; do
        if kubectl exec -n "$NAMESPACE" deployment/gitea -- curl -s -o /dev/null -w "%{http_code}" "http://localhost:3000/api/v1/version" | grep -q "200"; then
            break
        fi
        echo "        等待 Gitea... ($i/30)"
        sleep 5
    done
    
    echo "      创建 Gitea token..."
    TOKEN_RESPONSE=$(kubectl exec -n "$NAMESPACE" deployment/gitea -- curl -s -u "$GITEA_USER:$ITEA_PASS" \
        -X POST "http://localhost:3000/api/v1/users/$GITEA_USER/tokens" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"vibe-kanban-$(date +%s)\",\"scopes\":[\"write:repository\"]}")
    
    TOKEN=$(echo "$TOKEN_RESPONSE" | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4)
    
    if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
        echo "      错误: 无法创建 token"
        echo "      响应: $TOKEN_RESPONSE"
        exit 1
    fi
    
    kubectl create secret generic vibe-kanban-gitea \
        -n "$NAMESPACE" \
        --from-literal=token="$TOKEN" \
        --from-literal=username="$GITEA_USER" \
        --from-literal=url="$GITEA_URL"
    
    echo "      Token 创建成功"
else
    echo "      复用已有的 Gitea token"
    TOKEN=$(kubectl get secret vibe-kanban-gitea -n "$NAMESPACE" -o jsonpath='{.data.token}' | base64 -d)
fi

# 4. 推送本地项目到 Gitea（使用标准 Git HTTP 推送，触发流水线钩子）
echo "[4/6] 推送本地项目到 Gitea..."

chmod +x "${SCRIPT_DIR}/push-to-gitea-api.sh"

for project in ttpos-flutter ttpos-server-go; do
    PROJECT_PATH="${PROJECT_ROOT}/projects/${project}"
    
    if [ ! -d "$PROJECT_PATH" ]; then
        echo "      跳过 $project: 目录不存在"
        continue
    fi
    
    echo "      推送 $project..."
    "${SCRIPT_DIR}/push-to-gitea-api.sh" "$project" "$NAMESPACE" "$GITEA_USER" "$ITEA_PASS" 2>&1 | tail -3
done

echo "      所有项目已推送到 Gitea（流水线已触发）"

# 5. 加载镜像到 kind
echo "[5/6] 检查镜像..."
if docker images vibe-kanban:claude-test --format "{{.Repository}}" 2>/dev/null | grep -q "vibe-kanban"; then
    echo "      加载镜像到 kind..."
    kind load docker-image vibe-kanban:claude-test --name kind 2>/dev/null || true
else
    echo "      镜像不存在，使用默认镜像"
fi

# 6. 部署 vibe-kanban
echo "[6/6] 部署 Vibe Kanban..."
helm upgrade --install vibe-kanban "${PROJECT_ROOT}/charts/vibe-kanban" \
    --namespace "$NAMESPACE" \
    --values "${PROJECT_ROOT}/values/vibe-kanban-local.yaml" \
    --wait

echo ""
echo "========================================"
echo "  部署完成!"
echo "========================================"
echo ""
echo "查看状态:"
echo "  kubectl get pods -n $NAMESPACE"
echo ""
echo "查看 Clone 状态:"
echo "  kubectl logs -n $NAMESPACE job/vibe-kanban-clone-projects"
echo ""
echo "查看项目:"
echo "  kubectl exec -n $NAMESPACE deployment/vibe-kanban -- ls -la /projects"
echo ""
echo "Gitea 仓库:"
echo "  http://gitea-http.$NAMESPACE.svc.cluster.local:3000/gitea_admin/ttpos-flutter"
echo "  http://gitea-http.$NAMESPACE.svc.cluster.local:3000/gitea_admin/ttpos-server-go"
echo ""
