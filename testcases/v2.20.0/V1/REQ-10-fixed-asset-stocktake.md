# REQ-10 固定资产盘点

| Field | Value |
|-------|-------|
| ID | REQ-10 |
| Version | v2.20.0 |
| Priority | P2 |
| Difficulty | Easy |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

盘点模块新增"固定资产"(Property) 盘点类型，增加估值率为 0 的配置开关，并优化盘点单提交校验和 ERP 同步。

---

## 1. User Stories

### US-10.01

**As a** 仓库管理员
**I want to** 创建固定资产类型的盘点单
**So that** 我可以对固定资产（设备、家具等）进行独立盘点

### US-10.02

**As a** 仓库管理员
**I want to** 配置盘点是否允许估值率为 0
**So that** 对于没有市场估值的物品也能完成盘点

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-10.01 | 固定资产盘点类型 | 盘点类型增加 Property |
| FR-10.02 | 估值率为 0 开关 | 盘点允许估值率为 0 的配置开关 |
| FR-10.03 | 提交校验 | 盘点单提交时校验估值率（根据开关判断） |
| FR-10.04 | 盘点目的同步 ERP | 盘点目的字段同步到 ERPNext |
| FR-10.05 | 盘点出入库记录 | 恢复盘点出入库记录功能 |
| FR-10.06 | 草稿数量修复 | 修改数量后 check_materials 使用最新数量 |
| FR-10.07 | 版本检查 | 盘点模块最低版本 ≥2.20.14 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-10.01 | 估值率校验 | 盘点提交时，如果允许估值率为 0 开关关闭，则估值率为 0 的物品不允许提交 |
| BR-10.02 | 草稿数量 | 草稿修改数量后，盈亏判断必须基于最新数量 |

---

## 4. Acceptance Criteria

### AC-10.01 固定资产盘点

- **Given** 用户创建盘点单并选择"固定资产"类型
- **When** 提交盘点单
- **Then** 盘点类型字段为 Property，数据正确同步至 ERPNext

### AC-10.02 估值率为 0

- **Given** 商家开启了"允许估值率为 0"开关
- **When** 提交包含估值率为 0 的盘点单
- **Then** 校验通过，正常提交

---

## 5. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| ERPNext | 外部系统 | 盘点目的和结果同步至 ERPNext |
