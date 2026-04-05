# REQ-06 品牌采购自动审批

| Field | Value |
|-------|-------|
| ID | REQ-06 |
| Version | v2.20.0 |
| Priority | P1 |
| Difficulty | Medium |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

品牌采购门店审核通过后，根据总部自动审核设置，系统自动审批并提交 Sales Order、生成 Delivery Note，缩短采购流转时间。

---

## 1. User Stories

### US-06.01

**As a** 总部采购经理
**I want to** 设置品牌采购自动审批规则
**So that** 门店常规采购申请可以自动审批，加快流转速度

### US-06.02

**As a** 门店经理
**I want to** 提交的采购申请在满足条件时自动审批并生成 SO 和 DN
**So that** 我不用等待总部人工审批，缩短采购周期

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-06.01 | 自动审核开关 | 总部可设置是否自动审批门店采购 |
| FR-06.02 | 自动提交 SO | 全直采物品时自动审核并提交 Sales Order |
| FR-06.03 | 全直送自动提交 | 全直送订单 SO 直接提交 |
| FR-06.04 | 自动生成 DN | 审核通过后自动生成 Delivery Note |
| FR-06.05 | 物品默认仓库 | 取消门店"来源仓库"选择，按物品默认仓库拆分 SO |
| FR-06.06 | 多语言日志 | 采购日志"品牌采购自动审批"支持多语言 |
| FR-06.07 | ERP 上下文 | 自动审批需携带 ERP 上下文信息 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-06.01 | 全直送判断 | 如果所有物品都是直送类型，SO 直接提交无需额外审核 |
| BR-06.02 | 仓库拆分 | SO 按物品的默认仓库进行拆分，而非门店手动选择 |
| BR-06.03 | ERP 上下文 | 自动审批操作必须携带 ERP 上下文，否则 ERP API 调用失败 |
| BR-06.04 | 日志多语言 | 自动审批日志消息需支持中/英/泰等多语言 |

---

## 4. Acceptance Criteria

### AC-06.01 全直送自动审批

- **Given** 总部已开启自动审批，门店提交了全直送物品的采购申请
- **When** 门店审核通过
- **Then** 系统自动审批，SO 直接提交，DN 自动生成

### AC-06.02 物品默认仓库

- **Given** 物品 A 默认仓库为仓库 X，物品 B 默认仓库为仓库 Y
- **When** 采购申请包含 A 和 B
- **Then** SO 按仓库拆分为两个，分别关联仓库 X 和仓库 Y

---

## 5. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-05 自动收货 | 下游 | DN 到货后可触发自动收货 |
| ERPNext | 外部系统 | SO/DN 创建依赖 ERPNext API |

---

## 6. Key Files

- `main/app/service/purchase_order/purchase_order.go` — 采购订单主逻辑
- `main/app/service/purchase_order/helper.go` — 辅助函数
