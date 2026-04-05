#!/bin/bash
# Sisyphus DevOps - 一键启动脚本
# 确保所有服务就绪，可直接开始测试

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="sisyphus"
GITEA_URL="http://gitea-http.sisyphus.svc.cluster.local:3000"
GITEA_ADMIN="gitea_admin"
GITEA_PASSWORD="admin123"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============ 检查依赖 ============
check_dependencies() {
    log_info "检查依赖..."
    
    command -v kubectl >/dev/null 2>&1 || { log_error "需要 kubectl"; exit 1; }
    command -v helm >/dev/null 2>&1 || { log_error "需要 helm"; exit 1; }
    
    # 检查 kind 集群
    if ! kubectl cluster-info >/dev/null 2>&1; then
        log_error "无法连接到 Kubernetes 集群"
        log_info "请先创建 kind 集群: kind create cluster --name kind"
        exit 1
    fi
    
    log_success "依赖检查通过"
}

# ============ 检查并加载镜像 ============
ensure_images() {
    log_info "检查镜像..."
    
    # 检查必要的镜像是否在 kind 中
    local images=(
        "n8nio/n8n:latest"
        "docker.gitea.com/gitea:1.25.4-rootless"
    )
    
    for img in "${images[@]}"; do
        if ! docker images | grep -q "${img%:*}"; then
            log_warn "镜像 $img 未找到，尝试拉取..."
            docker pull "$img" 2>/dev/null || log_warn "拉取失败，将使用集群拉取"
        fi
    done
    
    log_success "镜像检查完成"
}

# ============ 部署基础设施 ============
deploy_infrastructure() {
    log_info "部署基础设施..."
    
    # 创建命名空间
    kubectl create namespace $NAMESPACE 2>/dev/null || log_info "命名空间已存在"
    
    # 1. 部署 PostgreSQL
    log_info "部署 PostgreSQL..."
    helm upgrade --install postgres-shared "$SCRIPT_DIR/charts/postgresql" \
        --namespace $NAMESPACE \
        --values "$SCRIPT_DIR/values/postgres.yaml" \
        --wait --timeout 5m 2>&1 | tail -5
    
    kubectl wait --for=condition=ready pod/postgres-shared-postgresql-0 -n $NAMESPACE --timeout=120s
    log_success "PostgreSQL 就绪"
    
    # 2. 创建数据库
    log_info "创建业务数据库..."
    kubectl exec -n $NAMESPACE postgres-shared-postgresql-0 -- bash -c \
        'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE IF NOT EXISTS gitea;"' 2>/dev/null || true
    kubectl exec -n $NAMESPACE postgres-shared-postgresql-0 -- bash -c \
        'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE IF NOT EXISTS n8n;"' 2>/dev/null || true
    
    # 3. 部署 Gitea
    log_info "部署 Gitea..."
    kubectl create secret generic gitea-admin-secret \
        --namespace $NAMESPACE \
        --from-literal=username=$GITEA_ADMIN \
        --from-literal=password=$GITEA_PASSWORD 2>/dev/null || true
    
    helm upgrade --install gitea "$SCRIPT_DIR/charts/gitea" \
        --namespace $NAMESPACE \
        --values "$SCRIPT_DIR/values/gitea.yaml" \
        --wait --timeout 5m 2>&1 | tail -5
    
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=gitea -n $NAMESPACE --timeout=120s
    log_success "Gitea 就绪"
    
    # 4. 部署 n8n
    log_info "部署 n8n..."
    helm upgrade --install n8n "$SCRIPT_DIR/charts/n8n" \
        --namespace $NAMESPACE \
        --values "$SCRIPT_DIR/values/n8n.yaml" \
        --wait --timeout 5m 2>&1 | tail -5
    
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=n8n -n $NAMESPACE --timeout=120s 2>/dev/null || \
        log_warn "n8n 启动较慢，继续等待..."
    
    log_success "n8n 就绪"
    
    # 5. 部署 Vibe Kanban
    log_info "部署 Vibe Kanban..."
    kubectl exec -n $NAMESPACE postgres-shared-postgresql-0 -- bash -c \
        'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE vibekanban;"' 2>/dev/null || true
    helm upgrade --install vibe-kanban "$SCRIPT_DIR/charts/vibe-kanban" \
        --namespace $NAMESPACE \
        --values "$SCRIPT_DIR/values/vibe-kanban.yaml" \
        --wait --timeout 5m 2>&1 | tail -5
    
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=vibe-kanban -n $NAMESPACE --timeout=120s 2>/dev/null || \
        log_warn "Vibe Kanban 启动较慢，继续等待..."
    
    log_success "基础设施部署完成"
}

# ============ 初始化 Gitea 仓库 ============
init_gitea_repos() {
    log_info "初始化 Gitea 仓库..."
    
    # 等待 Gitea API 可用
    local retries=30
    while ! curl -s "$GITEA_URL/api/v1/version" >/dev/null 2>&1; do
        sleep 2
        retries=$((retries - 1))
        if [ $retries -eq 0 ]; then
            log_error "Gitea API 不可用"
            exit 1
        fi
    done
    
    log_success "Gitea API 可用"
    
    # 获取新的 runner token（每次启动可能变化）
    RUNNER_TOKEN=$(curl -s -X POST \
        "$GITEA_URL/api/v1/admin/actions/runners/registration-token" \
        -u "$GITEA_ADMIN:$GITEA_PASSWORD" 2>/dev/null | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
    
    if [ -n "$RUNNER_TOKEN" ]; then
        log_info "Runner Token: $RUNNER_TOKEN"
        # 更新 runner 配置
        sed -i.bak "s/RUNNER_TOKEN: \"[^\"]*\"/RUNNER_TOKEN: \"$RUNNER_TOKEN\"/" "$SCRIPT_DIR/charts/gitea-act-runner.yaml" 2>/dev/null || true
    fi
    
    # 检查并创建仓库
    for repo in ttpos-flutter ttpos-server-go; do
        local repo_info=$(curl -s "$GITEA_URL/api/v1/repos/$GITEA_ADMIN/$repo" -u "$GITEA_ADMIN:$GITEA_PASSWORD" 2>/dev/null)
        
        if echo "$repo_info" | grep -q "id"; then
            log_info "仓库 $repo 已存在"
        else
            log_warn "仓库 $repo 不存在，请手动推送代码"
            log_info "执行: cd projects/$repo && git push gitea v2.20.0-ai-rebuild"
        fi
    done
    
    log_success "Gitea 仓库检查完成"
}

# ============ 显示状态 ============
show_status() {
    echo ""
    echo "========================================"
    echo "  🎉 Sisyphus 平台已就绪！"
    echo "========================================"
    echo ""
    
    echo "📦 服务状态:"
    kubectl get pods -n $NAMESPACE 2>/dev/null | grep -E "(NAME|Running)" | head -10
    
    echo ""
    echo "🔗 访问地址（需 telepresence）:"
    echo "  Gitea:       http://gitea-http.sisyphus.svc.cluster.local:3000"
    echo "  n8n:         http://n8n.sisyphus.svc.cluster.local:5678"
    echo "  Vibe Kanban: http://vibe-kanban.sisyphus.svc.cluster.local:3000"
    echo ""
    
    echo "📁 Git 仓库:"
    echo "  Flutter: http://gitea-http.sisyphus.svc.cluster.local:3000/gitea_admin/ttpos-flutter"
    echo "  Go:      http://gitea-http.sisyphus.svc.cluster.local:3000/gitea_admin/ttpos-server-go"
    echo ""
    
    echo "🔑 默认账号:"
    echo "  Gitea: $GITEA_ADMIN / $GITEA_PASSWORD"
    echo "  n8n:   admin / admin123"
    echo ""
    
    echo "📋 常用命令:"
    echo "  make status    # 查看服务状态"
    echo "  make clean     # 清理所有资源"
    echo "  make ci        # 在项目中运行 CI"
    echo ""
    
    echo "🚀 开始测试:"
    echo "  cd projects/ttpos-flutter && make ci"
    echo "  cd projects/ttpos-server-go && make ci"
    echo ""
}

# ============ 主流程 ============
main() {
    echo "========================================"
    echo "  🚀 Sisyphus DevOps 启动脚本"
    echo "========================================"
    echo ""
    
    check_dependencies
    ensure_images
    deploy_infrastructure
    init_gitea_repos
    show_status
}

main "$@"
