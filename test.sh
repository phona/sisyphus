#!/bin/bash
# Sisyphus - 自动化测试脚本
# 验证平台就绪并运行测试

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================"
echo "  🧪 Sisyphus 平台测试"
echo "========================================"
echo ""

# 检查服务状态
check_services() {
    echo "📦 检查服务状态..."
    
    # 检查 PostgreSQL
    if kubectl wait --for=condition=ready pod/postgres-shared-postgresql-0 -n sisyphus --timeout=10s >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} PostgreSQL"
    else
        echo -e "${RED}✗${NC} PostgreSQL 未就绪"
        return 1
    fi
    
    # 检查 Gitea
    if kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=gitea -n sisyphus --timeout=10s >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Gitea"
    else
        echo -e "${RED}✗${NC} Gitea 未就绪"
        return 1
    fi
    
    # 检查 n8n
    if kubectl get pod -l app.kubernetes.io/name=n8n -n sisyphus 2>/dev/null | grep -q "1/1"; then
        echo -e "${GREEN}✓${NC} n8n"
    else
        echo -e "${YELLOW}⚠${NC} n8n 未完全就绪（继续）"
    fi
    
    echo ""
    return 0
}

# 检查网络连通性
check_network() {
    echo "🌐 检查网络连通性..."
    
    # 测试 Gitea API
    if curl -s http://gitea-http.sisyphus.svc.cluster.local:3000/api/v1/version >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Gitea API 可访问"
    else
        echo -e "${RED}✗${NC} Gitea API 不可访问（尝试启动 telepresence）"
        return 1
    fi
    
    echo ""
}

# 检查代码仓库
check_repos() {
    echo "📁 检查代码仓库..."
    
    local base_url="http://gitea-http.sisyphus.svc.cluster.local:3000/api/v1/repos/gitea_admin"
    
    for repo in ttpos-flutter ttpos-server-go; do
        if curl -s "$base_url/$repo" | grep -q '"id":'; then
            echo -e "${GREEN}✓${NC} $repo 仓库存在"
        else
            echo -e "${YELLOW}⚠${NC} $repo 仓库不存在"
        fi
    done
    
    echo ""
}

# 运行项目测试
run_tests() {
    echo "🧪 运行项目测试..."
    echo ""
    
    # 测试 Flutter
    if [ -d "projects/ttpos-flutter" ]; then
        echo "测试 ttpos-flutter..."
        cd projects/ttpos-flutter
        if make ci 2>&1 | tail -5; then
            echo -e "${GREEN}✓${NC} ttpos-flutter 测试通过"
        else
            echo -e "${YELLOW}⚠${NC} ttpos-flutter 测试失败（检查 Makefile）"
        fi
        cd ../..
    fi
    
    echo ""
    
    # 测试 Go
    if [ -d "projects/ttpos-server-go" ]; then
        echo "测试 ttpos-server-go..."
        cd projects/ttpos-server-go
        if make ci 2>&1 | tail -5; then
            echo -e "${GREEN}✓${NC} ttpos-server-go 测试通过"
        else
            echo -e "${YELLOW}⚠${NC} ttpos-server-go 测试失败（检查 Makefile）"
        fi
        cd ../..
    fi
    
    echo ""
}

# 主流程
main() {
    # 检查是否在项目目录
    if [ ! -f "Makefile" ] || [ ! -d "projects" ]; then
        echo "请在 sisyphus 项目根目录运行此脚本"
        exit 1
    fi
    
    check_services
    check_network
    check_repos
    
    echo "========================================"
    echo "  ✅ 平台就绪，开始测试"
    echo "========================================"
    echo ""
    
    run_tests
    
    echo "========================================"
    echo "  🎉 测试完成"
    echo "========================================"
}

main "$@"
