# REQ-03 ERP Stock Entry 库存扣减

| Field | Value |
|-------|-------|
| ID | REQ-03 |
| Version | v2.20.0 |
| Priority | P0 |
| Difficulty | Medium |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

将库存扣减从混合流程中独立为专用 API，使用自定义 StockEntryType，提升可靠性和容错能力。

---

## 1. User Stories

### US-03.01

**As a** 仓库管理员
**I want to** 库存扣减由系统自动定时执行，而非混合在结账流程中
**So that** 即使结账高峰期库存扣减也不会成为瓶颈

### US-03.02

**As a** 运维人员
**I want to** Stock Entry 任务添加分布式锁
**So that** 多实例部署时不会重复扣减库存

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-03.01 | 独立扣减 API | 使用自定义 StockEntryType，不再复用通用接口 |
| FR-03.02 | 分布式锁 | Stock Entry 任务添加分布式锁，防止多实例重复执行 |
| FR-03.03 | 按门店时区触发 | 任务按门店所在时区的午夜触发 |
| FR-03.04 | 扣减容错 | 排除已处理 item 批次，跳过失败项继续处理 |
| FR-03.05 | NegativeStockError 解析 | 支持 ERPNext 库存不足错误解析和友好提示 |
| FR-03.06 | 待扣减量预计算 | SI 模式下加入待 SE 扣减量，防止库存虚高 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-03.01 | 时区感知 | 不同门店可能在不同时区，各自在本地午夜触发 |
| BR-03.02 | 失败跳过 | 单个 item 批次扣减失败不影响其他批次 |
| BR-03.03 | 虚高防护 | SyncWarehouseItemStock 在 SI 模式下包含待 SE 扣减量 |

---

## 4. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-03.01 | 幂等性 | 分布式锁保证同一任务不会被重复执行 |
| NFR-03.02 | 容错性 | 单项失败不中断整体批处理流程 |

---

## 5. Acceptance Criteria

### AC-03.01 独立扣减

- **Given** 订单已结账且生成了 SI
- **When** Stock Entry 定时任务在门店午夜触发
- **Then** 创建 StockEntryType 类型的 Stock Entry 扣减库存

### AC-03.02 分布式锁

- **Given** 两个服务实例同时运行
- **When** 到达触发时间
- **Then** 只有一个实例执行扣减任务，另一个跳过

### AC-03.03 库存不足

- **Given** ERPNext 返回 NegativeStockError
- **When** 系统解析错误
- **Then** 返回友好的库存不足提示，其他 items 继续处理

---

## 6. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-02 ERP SI/PE | 前置 | SI 模式下库存扣减依赖 SI 先创建成功 |
| ERPNext | 外部系统 | Stock Entry API |

---

## 7. Key Files

- `main/app/tasks/erp_stock_entry_task.go` — 定时任务
- `main/app/service/erp_stock_entry.go` — 扣减逻辑
- `main/app/model/stock_deduction_log.go` — 扣减日志
- `main/app/repository/sale_order.go` — WherePendingStockEntry()
