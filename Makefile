# Sisyphus DevOps - 统一 Makefile
.PHONY: help start stop status clean logs test-all
.PHONY: deploy-all deploy-postgres deploy-gitea deploy-n8n
.PHONY: init-repos push-all
.PHONY: telepresence-up telepresence-down

NAMESPACE := sisyphus
SCRIPT_DIR := $(shell pwd)

# 默认目标
help: ## 显示帮助信息
	@echo "🚀 Sisyphus DevOps - 可用命令"
	@echo ""
	@echo "【启动与管理】"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -E "(start|stop|status|clean|logs)" | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "【部署】"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -E "deploy" | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "【代码管理】"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -E "(init|push|repos)" | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "【测试】"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -E "test" | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", \$1, \$2}'
	@echo ""
	@echo "【网络】"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -E "telepresence" | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", \$1, \$2}'

# ========== 启动与管理 ==========

start: ## 一键启动所有服务（完整初始化）
	@echo "🚀 启动 Sisyphus 平台..."
	@chmod +x $(SCRIPT_DIR)/start.sh
	@$(SCRIPT_DIR)/start.sh

quick-start: ## 快速启动（跳过镜像检查）
	@echo "🚀 快速启动 Sisyphus..."
	@kubectl create namespace $(NAMESPACE) 2>/dev/null || true
	@$(MAKE) deploy-all
	@$(MAKE) status

stop: ## 停止所有服务（保留数据）
	@echo "🛑 停止服务..."
	@kubectl scale deployment gitea n8n --replicas=0 -n $(NAMESPACE) 2>/dev/null || true
	@echo "✅ 服务已停止，数据保留在 PostgreSQL PVC 中"

restart: ## 重启所有服务
	@echo "🔄 重启服务..."
	@$(MAKE) stop
	@sleep 3
	@$(MAKE) quick-start

status: ## 查看所有服务状态
	@echo "📦 Pods 状态:"
	@kubectl get pods -n $(NAMESPACE) 2>/dev/null || echo "命名空间不存在"
	@echo ""
	@echo "🔗 Services:"
	@kubectl get svc -n $(NAMESPACE) 2>/dev/null | grep -E "(gitea|n8n|postgres)"
	@echo ""
	@echo "💾 PVC 存储:"
	@kubectl get pvc -n $(NAMESPACE) 2>/dev/null || echo "无 PVC"

clean: ## 清理所有资源（⚠️ 数据会丢失）
	@echo "⚠️  确定要删除所有资源吗？数据将丢失！"
	@read -p "输入 'yes' 确认: " confirm && [ "$$confirm" = "yes" ] || exit 1
	@echo "🧹 清理资源..."
	@helm uninstall -n $(NAMESPACE) gitea n8n postgres-shared 2>/dev/null || true
	@kubectl delete namespace $(NAMESPACE) --wait=false 2>/dev/null || true
	@kubectl delete namespace gitea-runner --wait=false 2>/dev/null || true
	@echo "✅ 资源清理完成"

logs: ## 查看所有服务日志
	@echo "📜 最近的日志:"
	@kubectl logs -n $(NAMESPACE) -l app.kubernetes.io/name=gitea --tail=20 2>/dev/null || echo "Gitea 日志不可用"
	@echo "---"
	@kubectl logs -n $(NAMESPACE) -l app.kubernetes.io/name=n8n --tail=20 2>/dev/null || echo "n8n 日志不可用"

# ========== 部署 ==========

deploy-all: deploy-postgres deploy-gitea deploy-n8n deploy-vibe-kanban ## 部署所有组件

deploy-postgres: ## 部署 PostgreSQL
	@echo "📦 部署 PostgreSQL..."
	@helm upgrade --install postgres-shared $(SCRIPT_DIR)/charts/postgresql \
		--namespace $(NAMESPACE) --values $(SCRIPT_DIR)/values/postgres.yaml --wait
	@kubectl wait --for=condition=ready pod/postgres-shared-postgresql-0 -n $(NAMESPACE) --timeout=120s
	@echo "✅ PostgreSQL 就绪"

deploy-gitea: ## 部署 Gitea
	@echo "📦 部署 Gitea..."
	@kubectl create secret generic gitea-admin-secret \
		--namespace $(NAMESPACE) \
		--from-literal=username=gitea_admin \
		--from-literal=password=admin123 2>/dev/null || true
	@helm upgrade --install gitea $(SCRIPT_DIR)/charts/gitea \
		--namespace $(NAMESPACE) --values $(SCRIPT_DIR)/values/gitea.yaml --wait
	@kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=gitea -n $(NAMESPACE) --timeout=120s
	@echo "✅ Gitea 就绪"

deploy-n8n: ## 部署 n8n
	@echo "📦 部署 n8n..."
	@kubectl exec postgres-shared-postgresql-0 -n $(NAMESPACE) -- bash -c \
		'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE n8n;"' 2>/dev/null || true
	@helm upgrade --install n8n $(SCRIPT_DIR)/charts/n8n \
		--namespace $(NAMESPACE) --values $(SCRIPT_DIR)/values/n8n.yaml --wait
	@echo "✅ n8n 就绪"

deploy-vibe-kanban: ## 部署 Vibe Kanban（需要先构建镜像）
	@echo "📦 部署 Vibe Kanban..."
	@echo "⚠️  提示: 如果镜像不存在，请先执行 'make build-vibe-kanban-local'"
	@kubectl exec postgres-shared-postgresql-0 -n $(NAMESPACE) -- bash -c \
		'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE vibekanban;"' 2>/dev/null || true
	@helm upgrade --install vibe-kanban $(SCRIPT_DIR)/charts/vibe-kanban \
		--namespace $(NAMESPACE) --values $(SCRIPT_DIR)/values/vibe-kanban.yaml --wait
	@kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=vibe-kanban -n $(NAMESPACE) --timeout=120s || echo "⚠️ 等待中..."
	@echo "✅ Vibe Kanban 就绪"

deploy-vibe-kanban-local: ## 部署本地构建的 Vibe Kanban
	@echo "📦 部署本地 Vibe Kanban 镜像..."
	@kubectl exec postgres-shared-postgresql-0 -n $(NAMESPACE) -- bash -c \
		'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE vibekanban;"' 2>/dev/null || true
	@helm upgrade --install vibe-kanban $(SCRIPT_DIR)/charts/vibe-kanban \
		--namespace $(NAMESPACE) --values $(SCRIPT_DIR)/values/vibe-kanban-local.yaml --wait
	@kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=vibe-kanban -n $(NAMESPACE) --timeout=120s || echo "⚠️ 等待中..."
	@echo "✅ Vibe Kanban (本地镜像) 就绪"

build-vibe-kanban-local: ## 构建 Vibe Kanban 本地镜像（需要源码）
	@echo "🔧 构建 Vibe Kanban..."
	@echo "📝 步骤:"
	@echo "   1. git clone https://github.com/BloopAI/vibe-kanban.git /tmp/vibe-kanban"
	@echo "   2. cd /tmp/vibe-kanban && ./local-build.sh"
	@echo "   3. docker tag vibe-kanban:latest vibe-kanban:local"
	@echo "   4. kind load docker-image vibe-kanban:local --name kind"
	@echo "   5. make deploy-vibe-kanban-local"
	@echo ""
	@echo "📄 详细文档: docs/vibe-kanban-manual-build.md"
	@echo "🔄 开始自动构建..."
	@chmod +x $(SCRIPT_DIR)/build-vibe-kanban.sh && $(SCRIPT_DIR)/build-vibe-kanban.sh

logs-vibe-kanban: ## 查看 Vibe Kanban 日志
	@kubectl logs -n $(NAMESPACE) deployment/vibe-kanban -f --tail=100

deploy-runner: ## 部署 Gitea Actions Runner
	@echo "📦 部署 Gitea Runner..."
	@kubectl apply -f $(SCRIPT_DIR)/charts/gitea-act-runner.yaml
	@echo "✅ Runner 部署完成（需等待镜像加载）"

# ========== 代码管理 ==========

init-repos: ## 初始化 Gitea 仓库（首次推送代码）
	@echo "📁 初始化 Gitea 仓库..."
	@echo "推送 ttpos-flutter..."
	@cd $(SCRIPT_DIR)/projects/ttpos-flutter && \
		git remote add gitea http://gitea_admin:admin123@gitea-http.sisyphus.svc.cluster.local:3000/gitea_admin/ttpos-flutter.git 2>/dev/null || true && \
		git push gitea v2.20.0-ai-rebuild 2>/dev/null || echo "已推送或失败"
	@echo "推送 ttpos-server-go..."
	@cd $(SCRIPT_DIR)/projects/ttpos-server-go && \
		git remote add gitea http://gitea_admin:admin123@gitea-http.sisyphus.svc.cluster.local:3000/gitea_admin/ttpos-server-go.git 2>/dev/null || true && \
		git push gitea v2.20.0-ai-rebuild 2>/dev/null || echo "已推送或失败"
	@echo "✅ 仓库初始化完成"

push-all: ## 推送所有项目代码到 Gitea
	@echo "📤 推送所有项目..."
	@$(MAKE) push-flutter
	@$(MAKE) push-go
	@echo "✅ 全部推送完成"

push-flutter: ## 推送 Flutter 项目
	@echo "📤 推送 ttpos-flutter..."
	@cd $(SCRIPT_DIR)/projects/ttpos-flutter && git push gitea v2.20.0-ai-rebuild

push-go: ## 推送 Go 项目
	@echo "📤 推送 ttpos-server-go..."
	@cd $(SCRIPT_DIR)/projects/ttpos-server-go && git push gitea v2.20.0-ai-rebuild

# ========== 测试 ==========

test-all: test-flutter test-go ## 运行所有项目测试

test-flutter: ## 运行 Flutter 测试
	@echo "🧪 测试 ttpos-flutter..."
	@cd $(SCRIPT_DIR)/projects/ttpos-flutter && make ci

test-go: ## 运行 Go 测试
	@echo "🧪 测试 ttpos-server-go..."
	@cd $(SCRIPT_DIR)/projects/ttpos-server-go && make ci

# ========== 网络 ==========

telepresence-up: ## 启动 telepresence 连接
	@echo "🌐 启动 telepresence..."
	@telepresence connect
	@echo "✅ telepresence 已连接"
	@echo "访问地址:"
	@echo "  http://gitea-http.sisyphus.svc.cluster.local:3000"
	@echo "  http://n8n.sisyphus.svc.cluster.local:5678"

telepresence-down: ## 停止 telepresence
	@echo "🌐 停止 telepresence..."
	@telepresence quit
	@echo "✅ telepresence 已断开"

# ========== 调试 ==========

shell-postgres: ## 进入 PostgreSQL 命令行
	@kubectl exec -it postgres-shared-postgresql-0 -n $(NAMESPACE) -- psql -U postgres

shell-gitea: ## 进入 Gitea Pod
	@kubectl exec -it deployment/gitea -n $(NAMESPACE) -- bash

port-forward-gitea: ## 本地端口转发 Gitea (localhost:3000)
	@echo "🔗 转发 Gitea 到 localhost:3000"
	@kubectl port-forward -n $(NAMESPACE) svc/gitea-http 3000:3000

port-forward-n8n: ## 本地端口转发 n8n (localhost:5678)
	@echo "🔗 转发 n8n 到 localhost:5678"
	@kubectl port-forward -n $(NAMESPACE) svc/n8n 5678:5678
