# REQ-dedup-atomic-1777011303: fix(webhook): dedup at-least-once retry on handler crash

## 问题

BKD 重发同一 webhook 时，若首次处理过程中发生崩溃（handler 抛异常 / 5xx），`event_seen` 表中已有该 event_id 的记录（INSERT 成功），但 `processed_at` 为 NULL（handler 未跑完）。下次 BKD 重发时，原 dedup 逻辑判断 event 已存在就直接 skip，导致事件永久丢失，状态机卡死。

## 根因

原 `check_and_record` 返回 bool："INSERT 成功"和"webhook handler 跑完"不在同一事务。INSERT 后任何崩溃都会让 dedup 记录留下但实际未处理。

## 方案

### 数据模型变更

`event_seen` 表新增 `processed_at TIMESTAMPTZ NULL`：
- NULL = 首次处理崩溃，允许下次 BKD 重发 retry
- NOT NULL = 已成功处理，重发 skip

### dedup 语义变更

`check_and_record` 返回三值：`"new"` / `"retry"` / `"skip"`：
- `"new"`: INSERT 成功，全新事件，handler 跑
- `"retry"`: INSERT conflict + processed_at IS NULL，上次崩溃，handler 跑  
- `"skip"`: INSERT conflict + processed_at IS NOT NULL，已成功处理，skip

新增 `mark_processed`：handler 跑完调，设 processed_at = NOW()。

### webhook 改造

handler 各成功 return path 调 `mark_processed`；exception path 不调（让 BKD 重发走 retry）。

## 取舍

- **retry 安全性**：状态机 CAS 天然 idempotent。retry 路径若状态已推进，CAS 失败 skip，不双触发 action。
- **不修改 BKD 重发机制**：at-least-once delivery 保持不变，sisyphus 自己处理幂等。
- **不引入分布式锁**：保持简单，用 DB 行级状态代替显式锁。
