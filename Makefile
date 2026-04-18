# Sisyphus - 统一 Makefile
.PHONY: help test-all test-flutter test-go

SCRIPT_DIR := $(shell pwd)

# 默认目标
help: ## 显示帮助信息
	@echo "Sisyphus - 可用命令"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ========== 测试 ==========

test-all: test-flutter test-go ## 运行所有项目测试

test-flutter: ## 运行 Flutter 测试
	@echo "测试 ttpos-flutter..."
	@cd $(SCRIPT_DIR)/projects/ttpos-flutter && make ci

test-go: ## 运行 Go 测试
	@echo "测试 ttpos-server-go..."
	@cd $(SCRIPT_DIR)/projects/ttpos-server-go && make ci
