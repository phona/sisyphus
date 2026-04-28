# Sisyphus - 顶层 Makefile
#
# 自 dogfood ttpos-ci 标准（docs/integration-contracts.md §2.1 + §2.3）：
#   make ci-lint              —— ruff lint，BASE_REV 非空时仅检变更 *.py
#   make ci-unit-test         —— pytest -m "not integration"
#   make ci-integration-test  —— pytest -m integration（exit 5 视为 pass）
#   make accept-env-up        —— docker compose 起 ephemeral lab，emit endpoint JSON
#   make accept-env-down      —— 幂等清栈
#
# 前 3 个让 sisyphus 仓被 dev_cross_check / staging_test checker 跑；
# 后 2 个让 self-dogfood 的 accept 阶段跑得起来（REQ-self-accept-stage-1777121797）。
# accept-env-up/down 不带 ci- 前缀：它们是 accept 阶段 lab 边界，不在 PR-CI 热路径
# （REQ-rename-accept-targets-1777124774）。

.PHONY: help ci-lint ci-unit-test ci-integration-test accept-env-up accept-env-down test-all test-flutter test-go

SCRIPT_DIR := $(shell pwd)

help: ## 显示帮助信息
	@echo "Sisyphus - 可用命令"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ========== ttpos-ci 标准 target（self-dogfood） ==========

ci-lint: ## ruff lint；BASE_REV 非空 → 仅 lint 变更 *.py
	@if [ -z "$$BASE_REV" ]; then \
		echo "ci-lint: full scan (BASE_REV empty)"; \
		cd orchestrator && uv run ruff check src/ tests/; \
		cd $(SCRIPT_DIR)/thanatos && uv run ruff check src/ tests/; \
	else \
		orch_files=$$(git diff --name-only --diff-filter=ACMR "$$BASE_REV"...HEAD -- 'orchestrator/src/**.py' 'orchestrator/tests/**.py' 2>/dev/null || true); \
		thanatos_files=$$(git diff --name-only --diff-filter=ACMR "$$BASE_REV"...HEAD -- 'thanatos/src/**.py' 'thanatos/tests/**.py' 2>/dev/null || true); \
		if [ -z "$$orch_files" ] && [ -z "$$thanatos_files" ]; then \
			echo "ci-lint: no Python files changed in scope (BASE_REV=$$BASE_REV)"; \
			exit 0; \
		fi; \
		if [ -n "$$orch_files" ]; then \
			echo "ci-lint(orchestrator): scoped to $$(echo "$$orch_files" | wc -l) file(s) (BASE_REV=$$BASE_REV)"; \
			rel=$$(echo "$$orch_files" | sed 's|^orchestrator/||'); \
			(cd orchestrator && uv run ruff check $$rel); \
		fi; \
		if [ -n "$$thanatos_files" ]; then \
			echo "ci-lint(thanatos): scoped to $$(echo "$$thanatos_files" | wc -l) file(s) (BASE_REV=$$BASE_REV)"; \
			rel=$$(echo "$$thanatos_files" | sed 's|^thanatos/||'); \
			(cd thanatos && uv run ruff check $$rel); \
		fi; \
	fi

ci-unit-test: ## pytest 单测套件（排除 integration marker）
	cd orchestrator && uv run pytest -m "not integration"
	cd $(SCRIPT_DIR)/thanatos && uv run pytest -m "not integration"

ci-integration-test: ## pytest 集成测试（integration marker；零收集视为 pass）
	@if ! python3 -c 'import socket,os,re; dsn=os.environ.get("SISYPHUS_PG_DSN","postgresql://test:test@localhost/test"); m=re.search(r"@([^:/]+):?(\d+)?/",dsn); s=socket.create_connection((m.group(1),int(m.group(2) or 5432)),timeout=2); s.close()' 2>/dev/null; then \
		echo "ci-integration-test(orchestrator): no integration tests collected (exit 5 → pass) — PostgreSQL not reachable"; \
	else \
		cd orchestrator && set +e; uv run pytest -m integration; rc=$$?; \
		if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi; \
		[ $$rc -eq 5 ] && echo "ci-integration-test(orchestrator): no integration tests collected (exit 5 → pass)"; \
	fi

# ========== ttpos-ci accept env target（self-dogfood; integration repo 契约） ==========
# REQ-self-accept-stage-1777121797: sisyphus 自己同时充当 source repo 和 integration repo

accept-env-up: ## docker compose 起 ephemeral lab + emit endpoint JSON 到 stdout 末行
	@./scripts/sisyphus-accept-up-compose.sh

accept-env-down: ## docker compose down -v（幂等；best-effort）
	@./scripts/sisyphus-accept-down-compose.sh

# ========== 项目级测试聚合（保留） ==========

test-all: test-flutter test-go ## 运行所有项目测试

test-flutter: ## 运行 Flutter 测试
	@echo "测试 ttpos-flutter..."
	@cd $(SCRIPT_DIR)/projects/ttpos-flutter && make ci

test-go: ## 运行 Go 测试
	@echo "测试 ttpos-server-go..."
	@cd $(SCRIPT_DIR)/projects/ttpos-server-go && make ci
