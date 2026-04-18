# REQ-01 共享协议层抽取

| Field | Value |
|-------|-------|
| ID | REQ-01 |
| Priority | P0 |
| Difficulty | Easy |
| Scope | crosser-proto |
| Dependencies | None |

## Overview

从现有 `models/` 和 `utils/connector/` 中抽取共享协议定义，形成独立的 `crosser-proto` Go module。这是所有后续模块的基础依赖。

---

## 1. User Stories

### US-01.01

**As a** 开发者
**I want to** 在 crosser-proxy 和 crosser-api 中引用同一套消息定义
**So that** 两个模块的协议保持一致，不需要手动同步

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-01.01 | 消息类型定义 | 从 `models/message/messages.go` 抽取 `Message`、`ResultMessage` 和常量（LOGIN, HEART_BEAT, GEN_WORKER, AUTHENTICATION, SUCCESS, FAILED） |
| FR-01.02 | 错误码定义 | 从 `models/errors/errors.go` 抽取 `Error` 类型和错误码常量 |
| FR-01.03 | 配置结构体 | 从 `models/config/config.go` 抽取 `Config`、`ClientConfig`、`ServerConfig`、`AuthServerConfig` |
| FR-01.04 | Coordinator 接口 | 从 `utils/connector/coordinator.go` 抽取消息读写接口（可选：保留实现或仅抽接口） |
| FR-01.05 | Go Module 初始化 | `crosser-proto/go.mod`，module path: `github.com/phona/ubox-crosser/crosser-proto` |

---

## 3. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-01.01 | 零外部依赖 | proto 模块不依赖 shadowsocks、logrus 等，纯标准库 |
| NFR-01.02 | 向后兼容 | JSON 序列化格式与现有协议完全兼容 |

---

## 4. Acceptance Criteria

### AC-01.01 模块可独立编译

- **Given** `crosser-proto/` 目录存在
- **When** 执行 `cd crosser-proto && go build ./...`
- **Then** 编译成功，零错误

### AC-01.02 消息兼容性

- **Given** 使用新 proto 模块序列化 LOGIN 消息
- **When** 与旧格式对比
- **Then** JSON 输出完全一致：`{"type":0,"serve_name":"...","password":"..."}`

### AC-01.03 单元测试

- **Given** proto 模块
- **When** 运行 `go test ./...`
- **Then** 消息序列化/反序列化、错误码、配置 Update 方法全部通过

---

## 5. Key Files

### 源文件（从现有代码抽取）

- `models/message/messages.go` → `crosser-proto/message/`
- `models/errors/errors.go` → `crosser-proto/errors/`
- `models/config/config.go` → `crosser-proto/config/`

### 产出文件

```
crosser-proto/
├── go.mod
├── message/
│   └── message.go        # Message, ResultMessage, 常量
├── errors/
│   └── errors.go          # Error 类型, 错误码
└── config/
    └── config.go          # Config, ClientConfig, ServerConfig, AuthServerConfig
```
