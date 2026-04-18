# REQ-04 前端管理仪表盘

| Field | Value |
|-------|-------|
| ID | REQ-04 |
| Priority | P1 |
| Difficulty | Medium |
| Scope | crosser-web |
| Dependencies | REQ-03 |

## Overview

新建 `crosser-web` 前端项目，提供管理仪表盘 UI。使用 React + TypeScript + Vite，通过 crosser-api 的 REST API 实现服务管理、状态监控、用户管理功能。

---

## 1. User Stories

### US-04.01

**As a** 管理员
**I want to** 在浏览器中查看所有代理服务和在线状态
**So that** 不需要 SSH 登录服务器查看

### US-04.02

**As a** 管理员
**I want to** 通过 Web 界面创建和编辑代理服务配置
**So that** 操作更直观，降低配置出错概率

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-04.01 | 登录页 | 用户名密码登录，JWT 存储在 localStorage |
| FR-04.02 | 服务列表页 | 表格展示所有服务，含在线状态指示灯、连接数、操作按钮 |
| FR-04.03 | 服务编辑 | 创建/编辑服务的表单（name, address, cipher, passwords） |
| FR-04.04 | 状态总览 | 仪表盘首页：在线 proxy 数、总连接数、总流量 |
| FR-04.05 | Proxy 实例列表 | 查看每个 proxy 实例的地址、服务、最后心跳时间 |

---

## 3. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-04.01 | 响应式 | 适配桌面和平板 |
| NFR-04.02 | 构建产物 | 静态文件，可嵌入 crosser-api 通过 embed 分发 |

---

## 4. Acceptance Criteria

### AC-04.01 登录流程

- **Given** 打开仪表盘首页
- **When** 输入正确的用户名密码
- **Then** 跳转到仪表盘首页

### AC-04.02 服务管理

- **Given** 已登录
- **When** 点击「新建服务」，填写表单并提交
- **Then** 列表中出现新服务

### AC-04.03 在线状态

- **Given** proxy-server 已注册并在线
- **When** 查看服务列表
- **Then** 对应服务显示绿色在线指示灯和连接数

---

## 5. Pages

| 页面 | 路由 | 说明 |
|------|------|------|
| 登录 | `/login` | 用户名密码表单 |
| 仪表盘 | `/` | 状态总览卡片 |
| 服务列表 | `/services` | 服务表格 + CRUD |
| 服务详情 | `/services/:name` | 配置详情 + 连接统计 |
| Proxy 实例 | `/proxies` | 在线实例列表 |

---

## 6. Key Files

```
crosser-web/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   └── client.ts             # API 客户端封装
│   ├── pages/
│   │   ├── Login.tsx
│   │   ├── Dashboard.tsx
│   │   ├── ServiceList.tsx
│   │   ├── ServiceDetail.tsx
│   │   └── ProxyList.tsx
│   ├── components/
│   │   ├── StatusBadge.tsx
│   │   └── ServiceForm.tsx
│   └── hooks/
│       └── useAuth.ts
└── index.html
```
