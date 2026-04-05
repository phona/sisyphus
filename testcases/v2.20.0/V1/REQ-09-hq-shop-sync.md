# REQ-09 总店 → 子店设置强制推送

| Field | Value |
|-------|-------|
| ID | REQ-09 |
| Version | v2.20.0 |
| Priority | P1 |
| Difficulty | Medium |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

总部可将商品设置（价格、上下架、安全库存等）强制推送给子店，覆盖子店本地配置。支持物品级别的颗粒化推送，含负库存推送场景。

---

## 1. User Stories

### US-09.01

**As a** 总部运营经理
**I want to** 将商品价格变更强制推送到所有子店
**So that** 所有门店价格统一，无需逐个门店手动修改

### US-09.02

**As a** 总部运营经理
**I want to** 按物品级别推送设置（而非全量推送）
**So that** 只推送变更的物品，减少推送量和冲突

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-09.01 | 价格强制推送 | 总部价格变更强制覆盖子店 |
| FR-09.02 | 上下架强制推送 | 商品上下架状态强制同步 |
| FR-09.03 | 安全库存推送 | 推送时保留子店 override 设置 |
| FR-09.04 | 物品颗粒化推送 | 按物品级别推送，非全量（含负库存） |
| FR-09.05 | 并发优化 | 推送并发逻辑优化，子店判断优化 |
| FR-09.06 | 迁移兼容 | 迁移文件正确判断是否为子店 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-09.01 | 子店 override 保护 | 安全库存推送时保留子店已有的 override 值 |
| BR-09.02 | 颗粒化推送 | 仅推送有变更的物品，非全量覆盖 |
| BR-09.03 | 子店判断 | 迁移文件中需正确识别子店身份 |

---

## 4. Acceptance Criteria

### AC-09.01 价格推送

- **Given** 总部修改了商品 A 的价格
- **When** 触发强制推送
- **Then** 所有子店的商品 A 价格更新为总部价格

### AC-09.02 安全库存保留

- **Given** 子店对商品 B 设置了安全库存 override
- **When** 总部推送安全库存设置
- **Then** 子店的 override 值被保留，不被总部值覆盖

---

## 5. Dependencies

None.
