# REQ-13 POS 挂单优化

| Field | Value |
|-------|-------|
| ID | REQ-13 |
| Version | v2.20.0 |
| Priority | P2 |
| Difficulty | Easy |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

优化 POS 端挂单列表，增加流水号字段和搜索能力，移除设备过滤以支持会员端先下单后付订单的显示。

---

## 1. User Stories

### US-13.01

**As a** 收银员
**I want to** 在挂单列表中看到每笔订单的流水号并按流水号搜索
**So that** 我能快速找到特定订单

### US-13.02

**As a** 收银员
**I want to** 挂单列表也显示会员端的先下单后付订单
**So that** 我能统一管理所有待处理订单

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-13.01 | 挂单列表增加 order_no | 接口返回流水号字段 |
| FR-13.02 | 流水号搜索 | 挂单列表支持按流水号搜索 |
| FR-13.03 | 会员端订单显示 | 移除设备过滤，支持会员端先下单后付订单 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-13.01 | 无设备过滤 | 挂单列表不再按设备过滤，统一显示所有来源的挂单 |

---

## 4. Acceptance Criteria

### AC-13.01 流水号搜索

- **Given** 挂单列表有多笔订单
- **When** 输入流水号搜索
- **Then** 返回匹配的订单，列表显示 order_no 字段

### AC-13.02 会员端订单

- **Given** 会员端有先下单后付的挂单
- **When** 收银端加载挂单列表
- **Then** 会员端订单出现在列表中

---

## 5. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-01 会员端堂食 | 关联 | 先下单后付订单需要在此列表显示 |

---

## 6. Key Files

- `main/app/service/` — 挂单服务
