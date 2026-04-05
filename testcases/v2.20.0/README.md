# v2.20.0 Requirements

> Period: 2026-03-04 ~ 2026-03-26 | 394 commits (125 feat / 169 fix / 80 infra)
> Source: git `eccbe3405..39f7d94d1`

## Requirements Index

| ID | Title | Priority | Difficulty | Status | Scope | Document |
|----|-------|----------|------------|--------|-------|----------|
| REQ-01 | 会员端堂食订单 | P0 | Hard | Done | main | [REQ-01-member-dine-in-order.md](./REQ-01-member-dine-in-order.md) |
| REQ-02 | ERP SI/PE 直出 | P0 | Hard | Done | main | [REQ-02-erp-sales-invoice.md](./REQ-02-erp-sales-invoice.md) |
| REQ-03 | ERP Stock Entry 扣减 | P0 | Medium | Done | main | [REQ-03-erp-stock-entry.md](./REQ-03-erp-stock-entry.md) |
| REQ-04 | LINE MAN 外卖集成 | P1 | Hard | Done | ttpos-bmp | [REQ-04-lineman-takeout.md](./REQ-04-lineman-takeout.md) |
| REQ-05 | 品牌采购自动收货 | P1 | Medium | Done | main | [REQ-05-auto-receipt.md](./REQ-05-auto-receipt.md) |
| REQ-06 | 品牌采购自动审批 | P1 | Medium | Done | main | [REQ-06-auto-approval.md](./REQ-06-auto-approval.md) |
| REQ-07 | QR PromptPay 支付 | P1 | Easy | Done | main | [REQ-07-qr-promptpay.md](./REQ-07-qr-promptpay.md) |
| REQ-08 | 营业状态管理 | P1 | Easy | Done | main | [REQ-08-business-status.md](./REQ-08-business-status.md) |
| REQ-09 | 总店→子店强制推送 | P1 | Medium | Done | main | [REQ-09-hq-shop-sync.md](./REQ-09-hq-shop-sync.md) |
| REQ-10 | 固定资产盘点 | P2 | Easy | Done | main | [REQ-10-fixed-asset-stocktake.md](./REQ-10-fixed-asset-stocktake.md) |
| REQ-11 | GORM 查询缓存 | P2 | Hard | Paused | main | [REQ-11-gorm-cache.md](./REQ-11-gorm-cache.md) |
| REQ-12 | 门店点餐码设置 | P1 | Easy | Done | main | [REQ-12-store-scan-order.md](./REQ-12-store-scan-order.md) |
| REQ-13 | POS 挂单优化 | P2 | Easy | Done | main | [REQ-13-pos-pending-order.md](./REQ-13-pos-pending-order.md) |
| REQ-14 | 可观测性 Metrics | P2 | Medium | Done | main, bmp | [REQ-14-observability.md](./REQ-14-observability.md) |
| REQ-15 | AI 智能采购分析 | P2 | Medium | Done | main | [REQ-15-ai-purchase.md](./REQ-15-ai-purchase.md) |

## By Priority

### P0 — Core

- **REQ-01** 会员端堂食订单 [Hard] — 完整点餐闭环（浏览→下单→支付→取餐）
- **REQ-02** ERP SI/PE 直出 [Hard] — 结账即生成 Sales Invoice + Payment Entry
- **REQ-03** ERP Stock Entry 扣减 [Medium] — 独立扣减 API + 分布式锁

### P1 — Important

- **REQ-04** LINE MAN 外卖集成 [Hard] — 新外卖平台全流程对接
- **REQ-05** 品牌采购自动收货 [Medium] — 收货规则配置 + 自动执行
- **REQ-06** 品牌采购自动审批 [Medium] — 自动审批 + SO + DN
- **REQ-07** QR PromptPay 支付 [Easy] — 新支付方式 + 拒单限制
- **REQ-08** 营业状态管理 [Easy] — 正式/测试营业数据分离
- **REQ-09** 总店→子店推送 [Medium] — 价格/上下架强制同步
- **REQ-12** 门店点餐码设置 [Easy] — 扫码点餐开关 + 权限

### P2 — Nice-to-have

- **REQ-10** 固定资产盘点 [Easy] — 新盘点类型
- **REQ-11** GORM 查询缓存 [Hard] — 已暂停
- **REQ-13** POS 挂单优化 [Easy] — 流水号搜索
- **REQ-14** 可观测性 [Medium] — Prometheus metrics
- **REQ-15** AI 智能采购 [Medium] — LLM 采购建议

## By Difficulty

### Hard (4)

- REQ-01, REQ-02, REQ-04, REQ-11

### Medium (5)

- REQ-03, REQ-05, REQ-06, REQ-09, REQ-14, REQ-15

### Easy (5)

- REQ-07, REQ-08, REQ-10, REQ-12, REQ-13

## Fix Distribution (Top 5)

| Module | Fixes | Main Issues |
|--------|-------|-------------|
| member | 15 | 订单状态流转、班次分配、价格显示 |
| erp | 15 | SI 创建失败、库存扣减、反结账 |
| auto-receipt | 8 | SQL 安全、仓库过滤、状态默认值 |
| order | 7 | 先下单后付状态混淆、金额计算 |
| migration | 5 | 字段兼容、权限去重 |
