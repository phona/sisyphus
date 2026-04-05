# REQ-02 ERP Sales Invoice / Payment Entry 直出

| Field | Value |
|-------|-------|
| ID | REQ-02 |
| Version | v2.20.0 |
| Priority | P0 |
| Difficulty | Hard |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

收银端结账订单直接生成 ERPNext Sales Invoice + Payment Entry，替代原来异步扣减模式。支持 SI/PI 两种模式按班次版本动态切换。

---

## 1. User Stories

### US-02.01

**As a** 财务人员
**I want to** 收银端每次结账自动在 ERPNext 生成 Sales Invoice 和 Payment Entry
**So that** 财务数据实时同步，无需手动录入，减少对账差异

### US-02.02

**As a** 系统管理员
**I want to** SI 创建失败时订单进入死信队列，可在管理端查看和重试
**So that** 不会丢失任何财务记录，异常可追溯可修复

### US-02.03

**As a** 收银员
**I want to** 反结账时系统自动取消 ERP 中的发票并恢复库存
**So that** ERP 库存与实际保持一致

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-02.01 | 结账即生成 SI+PE | 结账订单直接创建 Sales Invoice 和 Payment Entry |
| FR-02.02 | SI/PI 模式动态判断 | 按班次版本号判断使用 SI 还是 PI 模式 |
| FR-02.03 | 充值订单 Customer | 充值订单 ERPNext Customer 从 Default 改为 Member |
| FR-02.04 | 充值现金找零 | Payment Entry 金额不超过 SI outstanding |
| FR-02.05 | Free Item 100% 折扣 | 物料项标记免费并设置 100% 折扣，防止 ERPNext 回填价格 |
| FR-02.06 | Credit Note 折扣 | 退款时 free item 也设置 discount_percentage=100 |
| FR-02.07 | 反结账库存恢复 | 按扣减日志恢复库存，不依赖 erp_stock_deducted 标志 |
| FR-02.08 | 充值反结账 | 按 ErpProductsInvoiceName 判断是否取消发票 |
| FR-02.09 | SI 死信队列 | 创建失败的订单进入死信队列，管理端可查看和重试 |
| FR-02.10 | 自定义字段迁移 | SI 和 PE 添加自定义字段（外卖相关等） |
| FR-02.11 | 公司名统一 | SI 创建时统一使用全名或缩写，避免混用 |
| FR-02.12 | 外卖字段 JSON tag | 与 ERPNext 实际字段名保持一致 |
| FR-02.13 | 扫码点餐发票 | 扫码点餐订单也生成 ERP 发票 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-02.01 | SI/PI 模式切换 | shift_version >= 新版阈值使用 SI 模式，否则使用 PI 模式 |
| BR-02.02 | Free Item 价格保护 | 所有免费物料项必须设置 discount_percentage=100，防止 ERPNext 回填默认价格 |
| BR-02.03 | 充值 Customer | 充值订单的 ERPNext Customer 使用 Member 类型，而非 Default |
| BR-02.04 | 找零金额限制 | Payment Entry 金额不能超过 SI 的 outstanding amount |
| BR-02.05 | 反结账恢复依据 | 反结账时按 stock_deduction_log 恢复库存，而非 erp_stock_deducted 标志 |
| BR-02.06 | 公司名一致性 | 单次 SI 创建过程中，公司全名和缩写不可混用 |

---

## 4. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-02.01 | 死信队列可靠性 | SI 创建失败必须进入死信队列，不能丢失 |
| NFR-02.02 | 异步处理 | 充值订单反结账异步取消发票，不阻塞主流程 |
| NFR-02.03 | 错误解析 | 支持 NegativeStockError 解析，返回友好错误提示 |

---

## 5. Acceptance Criteria

### AC-02.01 正常结账生成 SI+PE

- **Given** 收银端完成结账操作
- **When** 结账请求到达后端
- **Then** ERPNext 中创建 Sales Invoice 和 Payment Entry，金额与结账金额一致

### AC-02.02 Free Item 折扣

- **Given** 订单包含免费商品（赠菜）
- **When** 生成 Sales Invoice
- **Then** 免费商品的 discount_percentage=100，ERPNext 不会回填价格

### AC-02.03 SI 创建失败处理

- **Given** ERPNext 服务不可用或返回错误
- **When** SI 创建失败
- **Then** 订单进入死信队列，管理端可查看失败原因并手动重试

### AC-02.04 反结账

- **Given** 订单已完成并生成了 SI+PE
- **When** 执行反结账操作
- **Then** ERP 中取消 SI，按扣减日志恢复库存，清除 SI/PE 名称和同步状态

### AC-02.05 充值订单找零

- **Given** 会员充值订单有现金找零
- **When** 生成 Payment Entry
- **Then** PE 金额不超过 SI outstanding，Customer 类型为 Member

---

## 6. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-03 Stock Entry | 关联 | SI 模式下库存通过 Stock Entry 异步扣减 |
| REQ-01 会员端堂食 | 被依赖 | 堂食订单结账触发 SI+PE 生成 |
| ERPNext | 外部系统 | 依赖 ERPNext API 的可用性和正确性 |

---

## 7. Out of Scope

- PI 模式的逐步淘汰时间线未确定
- 死信队列的自动重试策略未实现，需手动触发

---

## 8. Key Files

- `main/app/service/order_erp_sales_invoice.go` — SI 生成逻辑
- `main/app/service/order_manage.go` — ReturnSalesInvoice(), CancelSalesInvoice()
- `main/app/queue/erp/erp_sales_invoice_callback.go` — 回调队列
- `main/app/api/v1/admin/handler.go` — SI 死信管理入口
