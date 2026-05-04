# REQ-feat-router-telemetry-v3-1777866642 — router decode-fail telemetry (3-signal emit)

Closes phona/sisyphus#372.

## 现状

`webhook._derive_verifier_event` 解析 `session.completed` from a verifier
sub-issue 时若 `extract_decision_robust` 拿不到合规 decision JSON / tag，会
返回 `VERIFY_ESCALATE`。webhook 当前对该路径的可观测信号只有：

1. **`log.info("webhook.verifier.decision", ...)`** — INFO 级，被 ops loki
   noise 淹没。
2. **`obs.record_event("verifier.decision.parse_retry_exhausted", ...)`** —
   仅在 retry 用尽分支写入；retry_worthy=False（首轮无 decision 文本）路径
   完全无 obs 记录。
3. **`statusId=review`** push 到 BKD —— 用户能在 BKD UI "review" 列看到
   issue，但 *为什么* 进 review 完全不可见，需要拉源码 / 手 base64 decode 才
   能定位 router 在抱怨啥。

5/4 v5 dogfood 实测：verifier emit 不合规 `decision:pass` 字符串 tag → router
解析失败 → REQ ESCALATED 状态无 transition → **静默吞掉，零 BKD 反馈、零
queryable obs、零 warning 级 log**。30 分钟后才有人意识到流水线卡住。

## 修法（3 路 emit，互相兜底）

`router.derive_verifier_event` 返回 `VERIFY_ESCALATE` + `reason` 时，webhook
在确认 *不再 retry* 的终态点（retry_worthy=False 直接 escalate / retry 用尽
escalate）emit 三处信号：

1. **`stage_runs` 写一行 decode-fail event** —
   `stage="router_decode_fail"` / `outcome="silent_drop"` /
   `fail_reason=reason` / `context={"issue_id", "raw_tags", "verifier_stage"}`。
   复用既有 `stage_runs` schema，无新表 / 新列。
   Metabase 看板新查询可 `WHERE stage='router_decode_fail'` 一眼出列表。

2. **BKD verifier issue 加 tag + description 警告块** —
   PATCH 该 issue tags 追加 `router-decode-fail`，再 PATCH description
   追加格式化警告块（reason / 期望格式 / 实际 tags / 操作建议）。
   tag 是稳定信号（BKD `Issue` 一定有 `tags` 字段）；description PATCH 是
   best-effort（如果 BKD 版本不支持 description 更新就退化成 warning log）。

3. **`log.warning("router.decode_fail", ...)`** — WARNING 级结构化日志，loki
   alerting 可订阅。

三路任一落地都能让"卡住 30 分钟"压到"看 BKD 看板秒懂"。

## 不做的事

- **不新建 migration** — 复用 `stage_runs.context::jsonb`（既有，0016 加的）
  装 `issue_id` / `raw_tags`，不引入 `0017_router_decode_fail.sql`。
- **不在 retry-not-yet-exhausted 路径 emit** — 那条路径会走 follow_up 让
  agent 重输出，先给 agent 自救机会；只在终态 escalate 才 emit 三路信号。
- **不改 router.py** — telemetry 完全在 webhook 调用路径上，router 仍是纯
  解析层。
- **不引入新 Event / 新 ReqState** — `VERIFY_ESCALATE` 走原 transition，
  telemetry 只是 side-effect。
- **不动 watchdog / GC** — 跟 escalate / resume 路径正交。
