# REQ-05 品牌采购自动收货

| Field | Value |
|-------|-------|
| ID | REQ-05 |
| Version | v2.20.0 |
| Priority | P1 |
| Difficulty | Medium |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

品牌采购收货单支持配置自动收货规则，当收货条件满足时系统自动完成收货，减少门店人工操作。

---

## 1. User Stories

### US-05.01

**As a** 门店经理
**I want to** 配置品牌采购的自动收货规则
**So that** 常规采购到货后自动完成收货，减少人工操作

### US-05.02

**As a** 门店经理
**I want to** 查看自动收货的执行日志
**So that** 我能确认哪些收货已自动完成，哪些需要人工介入

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-05.01 | 规则 CRUD | 创建、查看、编辑、删除自动收货规则 |
| FR-05.02 | 规则字段 | 名称、关联门店、发货仓库、供应商、状态 |
| FR-05.03 | 规则名称长度限制 | 最大长度约束 |
| FR-05.04 | 仓库过滤 | 仅返回普通仓库，排除在途仓库和禁用仓库 |
| FR-05.05 | 仓库状态联动 | 仓库禁用时关联规则自动失效 |
| FR-05.06 | 仓库保存校验 | 禁用/删除的仓库不允许保存配置 |
| FR-05.07 | 状态默认值 | status=0 正确写入，不被数据库覆盖为 1 |
| FR-05.08 | 冲突提示 | 门店规则冲突时显示门店名称 |
| FR-05.09 | 自动收货任务 | 后台定时任务按规则自动执行收货 |
| FR-05.10 | 收货日志 | 记录自动收货执行结果 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-05.01 | 仓库类型限制 | 仅普通仓库可配置为发货仓库，在途仓库排除 |
| BR-05.02 | 仓库状态联动 | 仓库被禁用或删除后，关联规则自动失效 |
| BR-05.03 | 保存校验 | 保存时校验发货仓库状态，不允许引用无效仓库 |
| BR-05.04 | 状态默认值 | 创建规则 status=0（禁用）时，数据库不得将其转为 1（启用） |
| BR-05.05 | 规则名称限制 | 规则名称有最大长度约束，防止过长 |

---

## 4. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-05.01 | SQL 安全 | 禁止字符串拼接 SQL，使用参数化查询 |
| NFR-05.02 | 认知复杂度 | 代码认知复杂度符合 SonarQube 标准 |

---

## 5. Acceptance Criteria

### AC-05.01 创建规则

- **Given** 管理员进入自动收货规则配置页面
- **When** 填写规则名称、选择门店、发货仓库、供应商并保存
- **Then** 规则创建成功，状态默认为禁用，仓库列表不包含在途和禁用仓库

### AC-05.02 仓库禁用联动

- **Given** 已有规则关联了仓库 A
- **When** 仓库 A 被禁用
- **Then** 关联规则自动失效，自动收货任务跳过该规则

### AC-05.03 自动执行

- **Given** 已启用规则且规则匹配当前收货单
- **When** 定时任务触发
- **Then** 自动完成收货，记录执行日志

---

## 6. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| REQ-06 自动审批 | 关联 | 自动审批生成的 DN 到货后触发自动收货 |

---

## 7. Key Files

- `main/app/api/v1/shop/shop_auto_receipt.go` — API 入口
- `main/app/service/auto_receipt.go` — 服务层
- `main/app/tasks/auto_receipt_task.go` — 定时任务
- `main/app/repository/auto_receipt_rule_repo.go` — 数据层
