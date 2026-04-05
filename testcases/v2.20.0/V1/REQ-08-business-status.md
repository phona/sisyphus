# REQ-08 营业状态管理

| Field | Value |
|-------|-------|
| ID | REQ-08 |
| Version | v2.20.0 |
| Priority | P1 |
| Difficulty | Easy |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

新管理端增加"营业状态"字段，区分正式营业与测试营业。数据统计按状态分开计算，避免测试数据污染正式报表。

---

## 1. User Stories

### US-08.01

**As a** 平台管理员
**I want to** 为每个商家设置营业状态（正式营业/测试营业/停业）
**So that** 数据统计时能区分正式数据和测试数据，保证统计准确性

### US-08.02

**As a** 门店经理
**I want to** 看到当前营业状态提示
**So that** 我知道当前是正式营业还是测试模式，避免误操作

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-08.01 | 营业状态枚举 | 正式营业 / 测试营业 / 停业 |
| FR-08.02 | 统计数据过滤 | 按营业状态过滤，正式/测试数据分开统计 |
| FR-08.03 | 状态提示 | 门店端显示当前营业状态及提示信息 |
| FR-08.04 | Settings 支持 | settings 接口增加营业状态相关字段 |
| FR-08.05 | 管理端配置 | 新管理端可设置和查看营业状态 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-08.01 | 数据分离 | 正式营业和测试营业的统计数据分开计算 |
| BR-08.02 | 默认状态 | 新商家默认为测试营业 |

---

## 4. Acceptance Criteria

### AC-08.01 状态设置

- **Given** 管理员进入商家营业状态设置
- **When** 选择"正式营业"并保存
- **Then** 后续统计数据仅归入正式营业维度

### AC-08.02 状态提示

- **Given** 商家当前为测试营业状态
- **When** 门店端加载
- **Then** 页面显示"测试营业"提示信息

---

## 5. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-01 会员端堂食 | 关联 | 营业时间校验依赖营业状态 |

---

## 6. Key Files

- `main/app/model/business_status_period.go` — 模型
- `main/app/repository/business_status_period.go` — 数据层
- `main/app/service/company.go` — UpdateBusinessStatus(), GetBusinessStatus()
