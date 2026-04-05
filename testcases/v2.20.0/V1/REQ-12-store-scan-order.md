# REQ-12 门店点餐码设置

| Field | Value |
|-------|-------|
| ID | REQ-12 |
| Version | v2.20.0 |
| Priority | P1 |
| Difficulty | Easy |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

门店端和收银机端支持开启/关闭扫码点餐功能。包含权限控制（手机点餐权限）、多语言翻译、模块最低版本限制。设置默认全部开启。

---

## 1. User Stories

### US-12.01

**As a** 门店经理
**I want to** 在门店后台开启/关闭扫码点餐功能
**So that** 我可以控制是否接受顾客扫码点餐

### US-12.02

**As a** 顾客
**I want to** 知道当前门店是否支持扫码点餐
**So that** 我能确定是否可以使用该功能

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-12.01 | is_open_store_scan_order | shop 端、收银机端 base 接口返回该字段 |
| FR-12.02 | 门店点餐码配置 | shop 端可配置开关 |
| FR-12.03 | 默认开启 | 门店点餐设置值默认全部开启 |
| FR-12.04 | 多语言翻译 | 桌码点餐和门店点餐码多语言文案 |
| FR-12.05 | 操作日志 | 日志中"会员端"改为"门店点餐码" |
| FR-12.06 | 手机点餐权限 | 餐厅设置下新增手机点餐权限 |
| FR-12.07 | 模块最低版本 | 按模块设置最小可用版本 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-12.01 | 默认开启 | 新门店的扫码点餐设置默认开启 |
| BR-12.02 | 权限控制 | 需要手机点餐权限才能修改设置 |
| BR-12.03 | 版本检查 | 客户端版本低于模块最低版本时功能不可用 |

---

## 4. Acceptance Criteria

### AC-12.01 开关配置

- **Given** 管理员进入门店点餐码设置
- **When** 开启扫码点餐并保存
- **Then** shop 端和收银机端 base 接口返回 `is_open_store_scan_order = true`

### AC-12.02 默认值

- **Given** 新创建的门店
- **When** 首次加载门店点餐设置
- **Then** 所有开关默认为开启状态

---

## 5. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-01 会员端堂食 | 被依赖 | 堂食订单功能启用依赖此开关 |

---

## 6. Key Files

- `main/app/api/v1/shop/` — shop 端 API
- `main/app/service/` — 设置服务
