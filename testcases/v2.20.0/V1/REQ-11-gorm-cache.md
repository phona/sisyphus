# REQ-11 GORM 查询缓存

| Field | Value |
|-------|-------|
| ID | REQ-11 |
| Version | v2.20.0 |
| Priority | P2 |
| Difficulty | Hard |
| Status | Paused |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

基于 Redis 的 GORM 查询结果缓存层，使用 ZSET 实现 LRU 索引淘汰。支持商户级别的白名单（内测）和黑名单（禁用）控制。

> **当前状态**：已暂停。因业务复杂度无法覆盖所有场景（如事务中的读写一致性），暂时关闭缓存使用。

---

## 1. User Stories

### US-11.01

**As a** 系统
**I want to** 缓存高频查询的结果
**So that** 减少数据库压力，提升接口响应速度

### US-11.02

**As a** 运维人员
**I want to** 通过白名单和黑名单控制缓存的商户范围
**So that** 内测阶段只有指定商户启用缓存，降低风险

---

## 2. Implemented Features

| ID | 功能 | 说明 |
|----|------|------|
| FR-11.01 | ZSET LRU 淘汰 | Redis ZSET 实现索引 LRU 淘汰策略 |
| FR-11.02 | 商户白名单 | 内测阶段限制使用商户范围 |
| FR-11.03 | 商户黑名单 | 数据库级别禁用特定商户缓存 |
| FR-11.04 | 全局开关 | 缓存启用/禁用控制 |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-11.01 | 白名单优先 | 仅白名单中的商户启用缓存 |
| BR-11.02 | 黑名单覆盖 | 黑名单中的商户即使白名单包含也不启用 |
| BR-11.03 | 全局开关 | 全局关闭时所有商户缓存失效 |

---

## 4. Resumption Criteria

- [ ] 梳理所有需要缓存/失效的业务场景
- [ ] 解决事务中的读写一致性问题
- [ ] 制定缓存失效策略
- [ ] 灰度测试通过
