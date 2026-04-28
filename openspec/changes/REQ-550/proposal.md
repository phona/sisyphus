# REQ-550: webhook dedup 过度 mark_processed 导致合法事件丢失

## Problem

`webhook.py` 中多个早期 return 点（skip_no_req_tag / no_event_mapping /
skip_no_req_or_intent_tag / no_req_id）都会 `await dedup.mark_processed(pool, eid)`。

后果：
1. 第一次 webhook 到达时被错误标记为已处理
2. BKD 重发时 dedup 返回 skip
3. 合法事件永远丢失

根因：这些早期 skip 并非"成功处理完毕"，而是"暂时不满足处理条件"。
标记 processed 会阻断 BKD 的 at-least-once retry，尤其致命的是 tag 竞争场景
——BKD  webhook 可能在 tag 尚未写全时就发出，此时无 REQ tag 的合法事件被
噪声 filter 挡掉并错误标记，后续 retry 被 dedup 直接 skip，事件永久丢失。

## Solution

1. 移除 `webhook.py` 所有早期 skip return 路径上的 `mark_processed` 调用。
2. 唯一保留 `mark_processed` 的位置：在 `engine.step` 成功返回之后。
3. 这样，早期 skip 不修改 `processed_at`（保持 NULL），BKD 重发时
   `check_and_record` 返回 `retry`，handler 重新走完整流程。
4. 如果 retry 时 tags 已补齐，事件正常进入状态机；如果仍是噪声，
   再次 early skip 即可——开销极小，不会无限累积（BKD retry 有上限，
   且 event_id 包含 executionId/timestamp，同事件重发次数有限）。

## Scope

- `orchestrator/src/orchestrator/webhook.py`：移除 4 处 `mark_processed`
- `orchestrator/tests/test_contract_router_noise_filter.py`：更新 RNF-S1/S5 断言
- `orchestrator/tests/test_dedup.py`：新增 DEDUP-S7 场景（early skip → retry → success）
