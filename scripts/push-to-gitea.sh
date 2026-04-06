#!/bin/bash
# 推送代码到 Gitea 并触发流水线
# 用法: ./scripts/push-to-gitea.sh <project-name>

set -e

PROJECT="${1:-ttpos-flutter}"
NAMESPACE="${2:-sisyphus}"
GITEA_USER="${3:-gitea_admin}"
GITEA_PASS="${4:-admin123}"
GITEA_URL="http://gitea-http.${NAMESPACE}.svc.cluster.local:3000"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_PATH="${PROJECT_ROOT}/projects/${PROJECT}"

echo "========================================"
echo "  推送 ${PROJECT} 到 Gitea"
echo "========================================"

# 检查项目目录
if [ ! -d "$PROJECT_PATH" ]; then
    echo "错误: 项目目录不存在: $PROJECT_PATH"
    exit 1
fi

# 获取 Gitea Token
TOKEN=$(kubectl get secret vibe-kanban-gitea -n "$NAMESPACE" -o jsonpath='{.data.token}' | base64 -d 2>/dev/null)
if [ -z "$TOKEN" ]; then
    echo "创建 Gitea Token..."
    TOKEN_RESPONSE=$(kubectl exec -n "$NAMESPACE" deployment/gitea -- curl -s -u "$GITEA_USER:$GITEA_PASS" \
        -X POST "http://localhost:3000/api/v1/users/$GITEA_USER/tokens" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"push-$(date +%s)\",\"scopes\":[\"write:repository\"]}")
    TOKEN=$(echo "$TOKEN_RESPONSE" | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4)
    
    kubectl create secret generic vibe-kanban-gitea \
        -n "$NAMESPACE" \
        --from-literal=token="$TOKEN" \
        --from-literal=username="$GITEA_USER" \
        --from-literal=url="$GITEA_URL" 2>/dev/null || true
fi

# 检查仓库是否存在，不存在则创建
echo "检查仓库..."
REPO_EXISTS=$(curl -s -o /dev/null -w "%{http_code}" \
    -u "$GITEA_USER:$TOKEN" \
    "$GITEA_URL/api/v1/repos/$GITEA_USER/$PROJECT" 2>/dev/null || echo "404")

if [ "$REPO_EXISTS" != "200" ]; then
    echo "创建仓库 ${PROJECT}..."
    curl -s -X POST \
        -u "$GITEA_USER:$TOKEN" \
        "$GITEA_URL/api/v1/user/repos" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$PROJECT\",\"description\":\"$PROJECT\",\"private\":false}" > /dev/null
fi

# 使用 Git HTTP 推送（会触发 Gitea 钩子）
echo "推送代码..."
cd "$PROJECT_PATH"

# 获取当前分支
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# 检查是否是 submodule
if [ -f .git ] && grep -q "^gitdir:" .git; then
    echo "检测到 Git Submodule，使用特殊处理方式..."
    
    # 创建临时目录进行推送
    TEMP_DIR=$(mktemp -d)
    git archive "$CURRENT_BRANCH" | tar -x -C "$TEMP_DIR"
    
    cd "$TEMP_DIR"
    git init
    git config user.email "admin@local"
    git config user.name "Admin"
    git add .
    git commit -m "Initial commit from submodule"
    
    # 推送到 Gitea（使用 HTTP Basic Auth，会触发钩子）
    git remote add origin "http://${GITEA_USER}:${TOKEN}@${GITEA_URL#http://}/$GITEA_USER/$PROJECT.git"
    git push -u origin HEAD:master --force
    
    rm -rf "$TEMP_DIR"
else
    # 普通仓库直接推送
    git remote remove gitea-push 2>/dev/null || true
    git remote add gitea-push "http://${GITEA_USER}:${TOKEN}@${GITEA_URL#http://}/$GITEA_USER/$PROJECT.git"
    git push gitea-push "$CURRENT_BRANCH":master --force
fi

echo "✅ ${PROJECT} 推送完成，流水线应该已触发"
echo ""
echo "查看仓库: $GITEA_URL/$GITEA_USER/$PROJECT"
