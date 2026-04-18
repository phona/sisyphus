# REQ-05 全栈集成与 CI

| Field | Value |
|-------|-------|
| ID | REQ-05 |
| Priority | P1 |
| Difficulty | Medium |
| Scope | all |
| Dependencies | REQ-01, REQ-02, REQ-03, REQ-04 |

## Overview

将 4 个模块集成为完整平台：Go workspace 管理多模块、Docker Compose 全栈编排、CI 流水线适配多模块构建和测试、端到端集成测试验证全链路。

---

## 1. User Stories

### US-05.01

**As a** 开发者
**I want to** `go work sync` 一键同步所有模块依赖
**So that** 本地开发时模块间引用自动解析

### US-05.02

**As a** 运维人员
**I want to** `docker compose up` 一键拉起整个平台
**So that** 快速部署完整环境

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-05.01 | Go workspace | 根目录 `go.work` 管理 crosser-proto、crosser-proxy、crosser-api |
| FR-05.02 | Docker Compose | 编排 crosser-api + crosser-proxy（server/client/auth） + echo-server + crosser-web |
| FR-05.03 | CI 多模块 | GitHub Actions 并行构建和测试 proto / proxy / api / web |
| FR-05.04 | 端到端测试 | 全链路：API 创建服务 → proxy 启动注册 → client 连接 → web 查看在线 → 隧道数据通过 |
| FR-05.05 | Makefile | 根目录 Makefile 统一入口：`make build`、`make test`、`make up`、`make ci-*` |

---

## 3. Acceptance Criteria

### AC-05.01 本地构建

- **Given** clone 仓库
- **When** `make build`
- **Then** 编译出 crosser-api、crosser-proxy（3 个二进制）、crosser-web 静态文件

### AC-05.02 Docker Compose

- **Given** Docker 环境
- **When** `docker compose up`
- **Then** 所有服务启动，web 可通过 `http://localhost:3000` 访问

### AC-05.03 端到端测试

- **Given** Docker Compose 全栈运行
- **When** 执行端到端测试
- **Then** 以下流程全部通过：
  1. API 创建服务 → 200 OK
  2. proxy-server 启动并注册 → 状态 API 返回 online
  3. client 连接 proxy → 心跳正常
  4. 外部通过 auth-server 隧道访问 echo-server → 返回数据正确
  5. web 仪表盘显示正确的在线状态和连接数

### AC-05.04 CI 流水线

- **Given** 提交 PR
- **When** CI 触发
- **Then** 并行执行：proto build → proxy lint+test / api lint+test → web build → 端到端测试

---

## 4. Key Files

```
ubox-crosser/                     # 项目根目录
├── go.work                       # Go workspace
├── docker-compose.yml            # 全栈编排
├── Makefile                      # 统一入口
├── .github/workflows/
│   └── ci.yml                    # 多模块 CI
├── crosser-proto/
├── crosser-proxy/
├── crosser-api/
├── crosser-web/
└── tests/
    └── e2e/                      # 端到端测试
        └── platform_test.go
```

---

## 5. Docker Compose Design

```yaml
services:
  api:
    build: ./crosser-api
    ports: ["8080:8080"]
    volumes: ["./data:/data"]       # SQLite

  proxy-server:
    build: ./crosser-proxy
    command: ["server", "--config-file", "/etc/crosser/server.json"]
    environment:
      - CROSSER_API_URL=http://api:8080

  client:
    build: ./crosser-proxy
    command: ["client", "--config-file", "/etc/crosser/client.json"]

  auth-server:
    build: ./crosser-proxy
    command: ["auth_server", "--config-file", "/etc/crosser/auth_server.json"]

  echo-server:
    image: hashicorp/http-echo

  web:
    build: ./crosser-web
    ports: ["3000:80"]

  e2e-runner:
    build: ./crosser-proxy
    command: ["go", "test", "-tags", "e2e", "./tests/e2e/..."]
```

---

## 6. CI Pipeline Design

```
ci.yml
├── Phase 1: Build (Concurrent)
│   ├── proto-build        # cd crosser-proto && go build ./...
│   ├── web-lint           # cd crosser-web && npm ci && npm run lint
│
├── Phase 2: Test (Concurrent, depends on Phase 1)
│   ├── proxy-lint-test    # cd crosser-proxy && go vet && golangci-lint && go test
│   ├── api-lint-test      # cd crosser-api && go vet && golangci-lint && go test
│   ├── web-build          # cd crosser-web && npm run build
│
└── Phase 3: E2E (depends on Phase 2)
    └── e2e-test           # docker compose up + e2e test runner
```
