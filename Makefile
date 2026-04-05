# Sisyphus DevOps - Makefile

.PHONY: help all postgres gitea n8n status clean

NAMESPACE := sisyphus

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

all: postgres gitea n8n ## 一键部署所有组件

postgres: ## 部署 PostgreSQL
	helm upgrade --install postgres-shared charts/postgresql \
		--namespace $(NAMESPACE) --values values/postgres.yaml --wait
	kubectl wait --for=condition=ready pod/postgres-shared-postgresql-0 -n $(NAMESPACE) --timeout=120s

gitea: postgres ## 部署 Gitea
	kubectl create secret generic gitea-admin-secret --namespace $(NAMESPACE) \
		--from-literal=username=gitea_admin --from-literal=password=admin123 2>/dev/null || true
	helm upgrade --install gitea charts/gitea \
		--namespace $(NAMESPACE) --values values/gitea.yaml --wait

n8n: postgres ## 部署 n8n
	kubectl exec postgres-shared-postgresql-0 -n $(NAMESPACE) -- bash -c \
		'PGPASSWORD=devops psql -U postgres -c "CREATE DATABASE n8n;"' 2>/dev/null || true
	helm upgrade --install n8n charts/n8n \
		--namespace $(NAMESPACE) --values values/n8n.yaml --wait

status: ## 查看部署状态
	@kubectl get pods -n $(NAMESPACE)
	@echo ""
	@kubectl get svc -n $(NAMESPACE)

clean: ## 删除所有部署
	helm uninstall -n $(NAMESPACE) gitea n8n postgres-shared 2>/dev/null || true
	kubectl delete namespace $(NAMESPACE) --wait=false 2>/dev/null || true
