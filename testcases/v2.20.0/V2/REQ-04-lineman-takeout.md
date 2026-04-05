# REQ-04 LINE MAN 外卖集成

| Field | Value |
|-------|-------|
| ID | REQ-04 |
| Version | v2.20.0 |
| Priority | P1 |
| Difficulty | Hard |
| Status | Done |
| Scope | ttpos-bmp |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

对接 LINE MAN 外卖平台，实现 OAuth 认证、菜单同步、订单接收、状态更新全流程。架构上预留 Grab 等多平台扩展能力。

---

## 1. User Stories

### US-04.01

**As a** 商家
**I want to** 在 LINE MAN 平台上线我的菜单
**So that** LINE MAN 用户可以下单，订单直接进入 TTPOS 系统

### US-04.02

**As a** 收银员
**I want to** LINE MAN 订单自动出现在 TTPOS 接单列表中
**So that** 我无需同时操作两个系统

### US-04.03

**As a** 系统管理员
**I want to** 启用多个外卖渠道（LINE MAN + Grab）
**So that** 未来可以快速接入新平台

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-04.01 | OAuth 2.0 认证 | LINE MAN OAuth 授权流程 |
| FR-04.02 | 菜单同步 | TTPOS 商品 → LINE MAN 菜单同步触发 |
| FR-04.03 | 菜单状态管理 | LINE MAN 端菜单上下架状态同步 |
| FR-04.04 | 下单接收 | LINE MAN 订单创建后推送到 TTPOS |
| FR-04.05 | 订单状态 Webhook | LINE MAN 通过 Webhook 通知状态变更 |
| FR-04.06 | 多平台激活服务 | 支持 LINE MAN + Grab 多渠道激活与查询 |
| FR-04.07 | ERP POS Invoice | LINEMAN 订单生成 POS Invoice |
| FR-04.08 | 消息 message key | 发送订单消息时使用 message key 保证顺序 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-04.01 | 菜单同步触发 | 菜单变更需主动触发同步至 LINE MAN |
| BR-04.02 | 订单顺序性 | 使用 message key 保证同一订单的消息有序 |
| BR-04.03 | 多渠道独立 | LINE MAN 和 Grab 渠道配置相互独立 |

---

## 4. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-04.01 | Webhook 可靠 | 订单状态 Webhook 需支持重试机制 |
| NFR-04.02 | 可扩展性 | 架构支持快速接入新外卖平台 |

---

## 5. Acceptance Criteria

### AC-04.01 菜单同步

- **Given** 商家已在 LINE MAN 渠道激活
- **When** 触发菜单同步
- **Then** LINE MAN 端菜单与 TTPOS 商品一致，上下架状态同步

### AC-04.02 订单接收

- **Given** LINE MAN 用户下单
- **When** TTPOS 接收到订单推送
- **Then** 订单出现在收银端接单列表，包含完整商品和金额信息

### AC-04.03 状态同步

- **Given** TTPOS 中订单状态变更（接单/出餐/完成）
- **When** 状态变更发生
- **Then** 通过 Webhook 通知 LINE MAN 状态更新

---

## 6. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| LINE MAN API | 外部系统 | LINE MAN OAuth、订单、菜单 API |
| REQ-02 ERP SI/PE | 关联 | LINEMAN 订单需生成 POS Invoice |

---

## 7. Out of Scope

- Grab 渠道完整实现（仅架构预留）
- LINE MAN 促销和折扣同步

---

## 8. Key Files

- `ttpos-bmp/app/ttpos-takeout/internal/logic/lineman/lineman.go` — 核心逻辑
- `ttpos-bmp/app/ttpos-takeout/internal/logic/lineman/lineman_order.go` — 订单处理
- `ttpos-bmp/app/ttpos-takeout/internal/client/lineman/` — API 客户端
- `ttpos-bmp/app/ttpos-takeout/internal/controller/lineman/` — 控制器
