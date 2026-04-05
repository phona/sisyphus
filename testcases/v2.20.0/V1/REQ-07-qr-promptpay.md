# REQ-07 QR PromptPay 支付方式

| Field | Value |
|-------|-------|
| ID | REQ-07 |
| Version | v2.20.0 |
| Priority | P1 |
| Difficulty | Easy |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

会员端新增 QR PromptPay 支付方式（常量 Code=80），用于扫码点餐支付场景。后台可控制会员端是否显示该支付方式。QR PromptPay 支付的订单不允许拒单。

---

## 1. User Stories

### US-07.01

**As a** 顾客
**I want to** 在会员端扫码点餐时使用 QR PromptPay 支付
**So that** 我可以用泰国主流的扫码支付方式完成付款

### US-07.02

**As a** 商家
**I want to** QR PromptPay 支付的订单不可被拒单
**So that** 避免支付已完成但订单被拒导致退款纠纷

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-07.01 | 支付方式常量 | 新增 `PaymentMethodCodeQRPromptPay = 80` |
| FR-07.02 | 会员端支付控制 | 后台可控制会员端是否显示 QR PromptPay |
| FR-07.03 | 数据库迁移 | 支付方式相关数据表和种子数据 |
| FR-07.04 | 拒单限制 | QR PromptPay 支付的订单不可拒单 |
| FR-07.05 | H5 订单支付列表 | H5 订单详情增加支付方式列表 |
| FR-07.06 | can_reject 字段 | 未处理 H5 订单通知增加拒单能力标识 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-07.01 | 拒单限制 | 使用 QR PromptPay 支付的订单，`can_reject = false` |
| BR-07.02 | 显示控制 | 未开启的支付方式不在会员端显示 |

---

## 4. Acceptance Criteria

### AC-07.01 支付显示

- **Given** 商家已开启 QR PromptPay 支付方式
- **When** 顾客进入支付页面
- **Then** QR PromptPay 出现在可选支付方式列表中

### AC-07.02 拒单限制

- **Given** 订单使用 QR PromptPay 支付
- **When** 收银端尝试拒单
- **Then** 系统拒绝操作，返回不可拒单提示

---

## 5. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-01 会员端堂食 | 被依赖 | QR PromptPay 用于堂食支付 |

---

## 6. Key Files

- `main/app/constant/payment.go` — PaymentMethodCodeQRPromptPay = 80
- `main/app/service/payment_method.go` — 支付方式服务
- `main/app/service/order_manage.go` — IsExistQrPromptPay(), IsQrPromptPay()
