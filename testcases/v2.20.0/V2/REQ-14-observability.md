# REQ-14 可观测性 Metrics

| Field | Value |
|-------|-------|
| ID | REQ-14 |
| Version | v2.20.0 |
| Priority | P2 |
| Difficulty | Medium |
| Status | Done |
| Scope | main, ttpos-bmp |
| Source | git: eccbe3405 → 39f7d94d1 |

## Overview

集成 Prometheus 指标采集，覆盖 HTTP 请求监控和业务操作耗时监控。Main 和 BMP 模块均接入，为后续 Grafana 面板提供数据源。

---

## 1. User Stories

### US-14.01

**As a** 运维人员
**I want to** 通过 Prometheus 监控 HTTP 请求延迟和错误率
**So that** 我能及时发现和定位线上性能问题

### US-14.02

**As a** 产品经理
**I want to** 监控关键业务操作的耗时（如 H5 接单、会员堂食订单处理）
**So that** 我能评估用户体验并优化慢流程

---

## 2. Functional Requirements

| ID | 需求 | 说明 |
|----|------|------|
| FR-14.01 | Prometheus HTTP 指标 | HTTP 请求延迟、状态码、吞吐量采集 |
| FR-14.02 | 业务操作耗时 | 订单操作耗时采集（H5 接单、会员堂食、多终端） |
| FR-14.03 | BMP 模块指标 | BMP 模块全面指标覆盖 |

---

## 3. Non-functional Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| NFR-14.01 | 低开销 | 指标采集对业务接口延迟影响 < 1ms |
| NFR-14.02 | 标准格式 | 使用 Prometheus 标准格式，兼容 Grafana |

---

## 4. Acceptance Criteria

### AC-14.01 HTTP 指标

- **Given** 服务启动并暴露 metrics 端点
- **When** 发起 HTTP 请求
- **Then** Prometheus 可抓取到请求延迟、状态码分布等指标

### AC-14.02 业务指标

- **Given** 会员端堂食订单流程执行
- **When** 订单创建/接单/支付完成
- **Then** 对应的业务操作耗时指标被记录

---

## 5. Dependencies

None.

---

## 6. Key Files

- `main/pkg/metrics/metrics.go` — 指标定义
- `main/router/router.go` — HTTP 中间件集成
