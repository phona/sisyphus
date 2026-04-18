# REQ-03 控制面 API 服务

| Field | Value |
|-------|-------|
| ID | REQ-03 |
| Priority | P0 |
| Difficulty | Hard |
| Scope | crosser-api |
| Dependencies | REQ-01 |

## Overview

新建 `crosser-api` Go module，作为管理控制面。提供 REST API 管理代理服务、用户认证、密钥管理，接收 proxy 的注册和状态上报。使用 SQLite 作为存储。

---

## 1. User Stories

### US-03.01

**As a** 管理员
**I want to** 通过 API 创建和管理代理服务（增删改查）
**So that** 不需要手动编辑 JSON 配置文件

### US-03.02

**As a** 管理员
**I want to** 查看所有在线的 proxy 实例和 client 连接状态
**So that** 掌握全局代理拓扑

### US-03.03

**As a** 管理员
**I want to** 管理用户账号和 API 密钥
**So that** 只有授权用户能访问管理 API

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-03.01 | 用户认证 | JWT 登录 + Token 鉴权中间件 |
| FR-03.02 | 服务管理 CRUD | 代理服务的增删改查：name, address, cipher method, login/auth password |
| FR-03.03 | 密钥管理 | 每个服务的加密密钥生成、轮换、查看（脱敏） |
| FR-03.04 | Proxy 注册接口 | `POST /api/v1/proxy/register`：proxy-server 启动时调用，上报地址和服务列表 |
| FR-03.05 | 状态上报接口 | `POST /api/v1/proxy/heartbeat`：定期接收 proxy 的连接统计 |
| FR-03.06 | 在线状态查询 | `GET /api/v1/proxy/status`：返回所有 proxy 实例 + 关联的 client 连接数 |
| FR-03.07 | 配置导出 | `GET /api/v1/services/{name}/config`：导出与现有 JSON 配置格式兼容的配置 |
| FR-03.08 | 数据库 | SQLite，表：users, services, proxy_instances, connection_stats |

---

## 3. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-03.01 | 轻量 | 单二进制，嵌入 SQLite，零外部依赖 |
| NFR-03.02 | API 规范 | RESTful，JSON 响应 `{"code": 0, "message": "success", "data": {}}` |
| NFR-03.03 | 安全 | 密码 bcrypt 存储，密钥 API 返回时脱敏 |

---

## 4. Acceptance Criteria

### AC-03.01 用户认证

- **Given** 默认管理员账号 admin/admin
- **When** `POST /api/v1/auth/login` 正确密码
- **Then** 返回 JWT token

- **When** 用错误密码登录
- **Then** 返回 401

### AC-03.02 服务 CRUD

- **Given** 已登录管理员
- **When** 创建服务 `POST /api/v1/services` `{"name": "test_svc", "address": ":7000", "method": "chacha20"}`
- **Then** 返回服务详情（含自动生成的 key）

- **When** 查询服务列表 `GET /api/v1/services`
- **Then** 返回包含 test_svc 的列表

### AC-03.03 Proxy 注册

- **Given** crosser-api 运行中
- **When** proxy-server 启动并调用 `POST /api/v1/proxy/register`
- **Then** 状态查询 API 返回该 proxy 实例为 online

### AC-03.04 配置兼容

- **Given** 通过 API 创建了服务
- **When** `GET /api/v1/services/test_svc/config`
- **Then** 返回的 JSON 可直接作为 proxy-server 的配置文件使用

---

## 5. API Design

```
POST   /api/v1/auth/login              # 登录
POST   /api/v1/auth/refresh            # 刷新 token

GET    /api/v1/services                 # 服务列表
POST   /api/v1/services                 # 创建服务
GET    /api/v1/services/:name           # 服务详情
PUT    /api/v1/services/:name           # 更新服务
DELETE /api/v1/services/:name           # 删除服务
GET    /api/v1/services/:name/config    # 导出配置

POST   /api/v1/proxy/register          # proxy 注册
POST   /api/v1/proxy/heartbeat         # proxy 心跳上报
GET    /api/v1/proxy/status             # 在线状态查询
```

---

## 6. Database Schema

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    address TEXT NOT NULL DEFAULT ':7000',
    cipher_method TEXT DEFAULT '',
    cipher_key TEXT DEFAULT '',
    login_password TEXT NOT NULL,
    auth_password TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE proxy_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    services TEXT NOT NULL,          -- JSON array of service names
    status TEXT NOT NULL DEFAULT 'online',
    last_heartbeat INTEGER NOT NULL,
    registered_at INTEGER NOT NULL
);

CREATE TABLE connection_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_instance_id INTEGER REFERENCES proxy_instances(id),
    active_clients INTEGER DEFAULT 0,
    active_workers INTEGER DEFAULT 0,
    bytes_in INTEGER DEFAULT 0,
    bytes_out INTEGER DEFAULT 0,
    recorded_at INTEGER NOT NULL
);
```

---

## 7. Key Files

```
crosser-api/
├── go.mod                        # 依赖 crosser-proto
├── cmd/
│   └── api/main.go               # 入口
├── internal/
│   ├── handler/
│   │   ├── auth.go               # 登录/刷新
│   │   ├── service.go            # 服务 CRUD
│   │   └── proxy.go              # 注册/心跳/状态
│   ├── service/
│   │   ├── auth.go
│   │   ├── service.go
│   │   └── proxy.go
│   ├── repository/
│   │   ├── user.go
│   │   ├── service.go
│   │   └── proxy.go
│   ├── model/
│   │   └── models.go             # DB models
│   ├── middleware/
│   │   └── jwt.go                # JWT 鉴权
│   └── database/
│       ├── sqlite.go             # DB 初始化
│       └── migrations/
│           └── 001_init.sql
└── tests/
    └── api_test.go               # API 集成测试
```
