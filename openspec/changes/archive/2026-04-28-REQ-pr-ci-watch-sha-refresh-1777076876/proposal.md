# REQ-pr-ci-watch-sha-refresh-1777076876: fix(pr_ci_watch): re-fetch head SHA each polling tick

## 问题

`pr_ci_watch` 在启动时只拉一次 PR 的 head SHA，整个轮询周期都用这个固定的 SHA 查 check-runs。
如果 dev agent 在 CI 运行中途 force-push（更新代码修 bug），检查器会继续盯着旧 SHA 的 check-runs，
导致：
- 新 SHA 的 CI 结果被完全忽略
- 旧 SHA 的 CI 变绿 → 错误地 pass（实际新代码的 CI 可能还没跑完或失败）
- force-push 后无任何感知，整体轮询结果不可信

## 方案

每个 polling tick 重新拉一次 head SHA（`_get_pr_info`），检测变化后：
1. SHA 变化（force-push）→ 清空该 repo 的 check-runs 缓存，从新 SHA 重新轮询
2. 翻转次数超过 5 次（`_MAX_SHA_FLIPS=5`）→ fail，reason=`too-many-sha-flips`（防止无限 churn）
3. PR 被 merge → 立即 pass（merged → CI 必已过，或 repo 主动决策）
4. PR 被 close（未 merge）→ fail，reason=`pr-closed-without-merge`
5. refetch 失败（HTTP 错误 / PR 一时找不到）→ 警告 + retry，与 check-run API 错误语义一致

超时保持原有 1800s，不变。

## 范围

只改 `orchestrator/src/orchestrator/checkers/pr_ci_watch.py` 及其测试。
不改 schema、不改 Metabase 看板（只加 `log.info("sha_flip", ...)` 便于观测）。
