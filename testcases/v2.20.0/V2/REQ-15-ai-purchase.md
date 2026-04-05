# REQ-15 AI 智能采购分析

| Field | Value |
|-------|-------|
| ID | REQ-15 |
| Version | v2.20.0 |
| Priority | P2 |
| Difficulty | Medium |
| Status | Done |
| Scope | main |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

AI Agent 模块，基于 LLM 分析历史采购数据，智能推荐采购方案。支持环境变量兼容和默认仓库自动选择。

---

## 1. User Stories

### US-15.01

**As a** 采购经理
**I want to** 系统根据历史采购数据自动分析并推荐采购方案
**So that** 我不用手动分析大量历史数据，提高采购决策效率

### US-15.02

**As a** 系统管理员
**I want to** 兼容现有的 LLM 环境变量配置
**So that** 不需要重新配置就能使用 AI 功能

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-15.01 | AI 采购分析 | 调用 LLM 分析历史数据，生成采购建议 |
| FR-15.02 | 环境变量兼容 | 优先读 `AI_AGENT_LLM_*`，回退 `LLM_*` |
| FR-15.03 | 默认仓库 | `warehouse_uuid` 可选，不传自动使用默认仓库 |
| FR-15.04 | Swagger 注解 | API 注解改为引用结构体，兼容 Apifox |

---

## 3. Business Rules

| Rule ID | Rule | Description |
|---------|------|-------------|
| BR-15.01 | 环境变量回退 | 优先读取 `AI_AGENT_LLM_*` 环境变量，不存在时回退到 `LLM_*` |
| BR-15.02 | 默认仓库 | 未指定 warehouse_uuid 时自动使用物品的默认仓库 |

---

## 4. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-15.01 | API 文档 | Swagger 注解使用引用结构体，确保 Apifox 兼容 |

---

## 5. Acceptance Criteria

### AC-15.01 采购建议

- **Given** 系统有足够的历史采购数据
- **When** 用户触发 AI 采购分析
- **Then** 返回基于 LLM 分析的采购建议，包含推荐数量和理由

### AC-15.02 默认仓库

- **Given** 用户未指定 warehouse_uuid
- **When** 调用 AI 采购接口
- **Then** 系统自动使用默认仓库，不报错

---

## 6. Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| LLM API | 外部系统 | 依赖大语言模型 API 的可用性 |

---

## 7. Key Files

- `main/app/service/ai_agent/` — AI Agent 服务
