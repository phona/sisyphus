# REQ-02 数据面重构

| Field | Value |
|-------|-------|
| ID | REQ-02 |
| Priority | P0 |
| Difficulty | Hard |
| Scope | crosser-proxy |
| Dependencies | REQ-01 |

## Overview

将现有的 server/client/auth_server 代码重构为独立的 `crosser-proxy` Go module，引用 `crosser-proto` 共享协议，并增加以下能力：向控制面注册、上报状态、Prometheus 指标暴露、优雅关闭。

---

## 1. User Stories

### US-02.01

**As a** 运维人员
**I want to** proxy 启动后自动注册到控制面 API
**So that** 管理平台能感知所有在线的代理实例

### US-02.02

**As a** 运维人员
**I want to** 通过 Prometheus 监控代理的连接数和流量
**So that** 我能及时发现容量瓶颈

### US-02.03

**As a** 运维人员
**I want to** 发送 SIGTERM 后代理优雅关闭
**So that** 不会中断正在传输的隧道连接

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-02.01 | 模块独立化 | 将 `server/`、`client/`、`utils/`、`log/`、`cmd/` 迁移到 `crosser-proxy/`，引用 `crosser-proto` |
| FR-02.02 | 控制面注册 | proxy-server 启动时调用 crosser-api 的注册接口，上报地址和服务列表 |
| FR-02.03 | 状态上报 | 定期上报在线 client 列表、worker 连接数、字节流量到 crosser-api |
| FR-02.04 | Prometheus 指标 | 暴露 `/metrics` 端点：`crosser_active_clients`、`crosser_active_workers`、`crosser_bytes_transferred_total`、`crosser_auth_total{result="success\|failed"}` |
| FR-02.05 | 优雅关闭 | SIGTERM/SIGINT → 停止 accept → 等待现有连接结束（超时 30s）→ 退出 |
| FR-02.06 | 并发安全 | controllers map 加读写锁，修复现有竞态 |
| FR-02.07 | Health Check | HTTP `/healthz` 端点，返回 200 表示服务可用 |

---

## 3. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-02.01 | 兼容性 | 重构后的 proxy 协议层与现有 client/auth_server 完全兼容 |
| NFR-02.02 | 指标开销 | Prometheus 采集对隧道延迟影响 < 1ms |
| NFR-02.03 | 关闭时间 | 优雅关闭最大等待 30 秒 |

---

## 4. Acceptance Criteria

### AC-02.01 模块编译

- **Given** `crosser-proxy/` 依赖 `crosser-proto/`
- **When** `cd crosser-proxy && go build ./cmd/...`
- **Then** 编译出 client、server、auth_server 三个二进制

### AC-02.02 隧道兼容

- **Given** 重构后的 proxy + 现有测试配置
- **When** 运行集成测试（docker compose）
- **Then** 全链路隧道测试通过（auth-server → proxy → client → echo-server）

### AC-02.03 Prometheus 指标

- **Given** proxy-server 运行中，client 已连接
- **When** `curl http://proxy-server:9090/metrics`
- **Then** 返回 `crosser_active_clients 1`、`crosser_active_workers >= 0`

### AC-02.04 优雅关闭

- **Given** 隧道正在传输数据
- **When** 发送 SIGTERM 给 proxy-server
- **Then** 当前传输完成后进程退出，退出码 0

### AC-02.05 并发安全

- **Given** 多个 client 同时连接
- **When** 运行 `go test -race ./...`
- **Then** 无 race condition 报告

---

## 5. Key Files

### 产出结构

```
crosser-proxy/
├── go.mod                    # 依赖 crosser-proto
├── cmd/
│   ├── client/main.go
│   ├── server/main.go
│   └── auth_server/main.go
├── internal/
│   ├── server/
│   │   ├── proxy.go          # ProxyServer（从 server/server.go 重构）
│   │   ├── controller.go     # controller（加锁）
│   │   ├── auth.go           # AuthServer
│   │   └── pipe.go           # drillingTunnel
│   ├── client/
│   │   ├── client.go
│   │   └── controller.go
│   ├── connector/
│   │   ├── coordinator.go
│   │   ├── dispatcher.go
│   │   └── cipher.go
│   ├── metrics/
│   │   └── metrics.go        # Prometheus 指标定义
│   └── log/
│       └── log.go
└── tests/
    └── integration/           # 集成测试
```
