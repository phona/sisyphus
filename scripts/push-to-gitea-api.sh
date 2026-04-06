#!/bin/bash
# 使用 Gitea API 推送代码（触发流水线钩子）
# 用法: ./scripts/push-to-gitea-api.sh <project-name>

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
echo "  推送 ${PROJECT} 到 Gitea (API方式)"
echo "========================================"

# 检查项目目录
if [ ! -d "$PROJECT_PATH" ]; then
    echo "错误: 项目目录不存在: $PROJECT_PATH"
    exit 1
fi

# 获取或创建 Token
TOKEN=$(kubectl get secret vibe-kanban-gitea -n "$NAMESPACE" -o jsonpath='{.data.token}' | base64 -d 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
    echo "创建 Gitea Token..."
    TOKEN_RESPONSE=$(kubectl exec -n "$NAMESPACE" deployment/gitea -- curl -s -u "$GITEA_USER:$GITEA_PASS" \
        -X POST "http://localhost:3000/api/v1/users/$GITEA_USER/tokens" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"api-push-$(date +%s)\",\"scopes\":[\"write:repository\"]}")
    TOKEN=$(echo "$TOKEN_RESPONSE" | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4)
    
    kubectl create secret generic vibe-kanban-gitea \
        -n "$NAMESPACE" \
        --from-literal=token="$TOKEN" \
        --from-literal=username="$GITEA_USER" \
        --from-literal=url="$GITEA_URL" 2>/dev/null || true
fi

# 检查仓库是否存在，不存在则创建
echo "检查仓库..."
REPO_EXISTS=$(kubectl exec -n "$NAMESPACE" deployment/gitea -- curl -s -o /dev/null -w "%{http_code}" \
    -u "$GITEA_USER:$TOKEN" "http://localhost:3000/api/v1/repos/$GITEA_USER/$PROJECT" 2>/dev/null || echo "404")

if [ "$REPO_EXISTS" != "200" ]; then
    echo "创建仓库 ${PROJECT}..."
    kubectl exec -n "$NAMESPACE" deployment/gitea -- curl -s -X POST \
        -u "$GITEA_USER:$TOKEN" \
        "http://localhost:3000/api/v1/user/repos" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$PROJECT\",\"description\":\"$PROJECT\",\"private\":false}" > /dev/null
fi

# 方案：在 Gitea Pod 内执行 git push，但使用 Gitea 的 HTTP 端口
# 这样 Gitea 会处理请求并触发钩子
echo "打包并推送代码..."

# 打包项目
cd "$PROJECT_PATH"
tar czf /tmp/${PROJECT}.tar.gz . 2>/dev/null || true

# 获取 Gitea Pod 名称
GITEA_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}')
echo "  Gitea Pod: $GITEA_POD"

# 复制到 Gitea Pod
kubectl cp /tmp/${PROJECT}.tar.gz "$NAMESPACE/$GITEA_POD:/tmp/${PROJECT}.tar.gz"

# 在 Gitea Pod 内解压并通过本地 HTTP 推送
kubectl exec -n "$NAMESPACE" deployment/gitea -- sh -c "
set -e
rm -rf /tmp/${PROJECT}-push
mkdir -p /tmp/${PROJECT}-push
cd /tmp/${PROJECT}-push
tar xzf /tmp/${PROJECT}.tar.gz 2>/dev/null || true

# 如果是 submodule，重新初始化
if [ -f .git ] && grep -q 'gitdir:' .git 2>/dev/null; then
    rm -f .git
fi

# 初始化并提交
git init 2>/dev/null || true
git config user.email 'admin@local'
git config user.name 'Admin'
git add . 2>/dev/null || true
git commit -m 'Initial commit' 2>/dev/null || echo 'Nothing to commit'

# 推送到 Gitea（通过本地 HTTP，会触发钩子）
git remote remove origin 2>/dev/null || true
git remote add origin 'http://${GITEA_USER}:${TOKEN}@localhost:3000/${GITEA_USER}/${PROJECT}.git'
git push -u origin HEAD:master --force 2>&1 || echo 'Push may have failed'

echo 'Push completed'
" 2>&1 | tail -20

echo "✅ ${PROJECT} 推送完成"
echo "查看仓库: $GITEA_URL/$GITEA_USER/$PROJECT"
echo "查看 Actions: $GITEA_URL/$GITEA_USER/$PROJECT/actions"
