# REQ-450: improver-autopilot — sisyphus 自治改进环路 + 4 类白名单 rule + budget cap

## 问题

sisyphus 有完整的观测层（stage_runs / verifier_decisions / artifact_checks / req_state），
但改进动作靠人工看 Metabase 仪表盘然后手动改 config 或开 REQ。
结果是：信号积累了几天才被人发现，改进窗口经常被"周末 / 假期 / on-call 人力不足"打断。

具体痛点：
1. staging-test P95 超阈值 → watchdog 先 escalate → 人才发现 watchdog_stuck_threshold_sec 设太低
2. fixer-round-cap 被频繁命中 → 说明 fixer 轮上限太低，但人工改配置需要专门的 REQ
3. infra flake 高峰期 → checker_infra_flake_retry_max 打满后 escalate，而实际上 retry 再多跑一遍就过了
4. inflight-cap 被三次 escalate → inflight_req_cap 太低，高峰期 backlog 积压

## 方案

新增 `improver.py` 后台 daemon（与 watchdog / runner_gc 同级），周期（默认 24h）扫描 4 类信号：

| rule | 信号 | 动作 |
|---|---|---|
| `latency-guard` | stage P95 > 75% watchdog 阈值 | bump watchdog_stuck_threshold_sec +25%，max 14400s |
| `loop-cap` | fixer cap-hit 率 >30% | raise fixer_round_cap +1，max 10；max 观测轮数 < cap-2 时 lower -1，min 3 |
| `flake-tolerance` | 7d infra flake 率 >25% | raise checker_infra_flake_retry_max +1，max 3；14d 率 <3% 时 lower -1，min 0 |
| `throughput` | 7d inflight-cap escalation ≥3 次 | raise inflight_req_cap +2，range [5, 20] |

**两种模式**：
- detect-only（`improver_bkd_project_id` 空）：写 `improver_runs`（status=pending），只记录不开 issue
- autopilot（填 BKD project ID）：自动开 `intent:analyze` issue 让 analyze-agent 执行改进

**Budget cap**：
- 每 ISO 周 non-skipped 总量 ≤ `improver_budget_per_window`（默认 2）
- 每条 rule cooldown ≥ `improver_cooldown_per_rule_days`（默认 7d）
- 每条 rule 要求 sample count ≥ `improver_min_sample_count`（默认 20）

## 风险

1. **低质量信号开 issue** —— budget cap + cooldown + min sample guard 三重防护；默认 detect-only
2. **重复改同一 config** —— cooldown per rule 7d；improver_runs 表持久记录历史
3. **配置参数越界** —— 每条 rule 硬编码 min/max；超界 signal 不产生（skip）

## 不在本 REQ 范围

- 改配置的执行（analyze-agent 负责，sisyphus 只开 issue）
- 更多 rule 类型（如 cost-guard / quality-regression）
- 人工 approval flow for autopilot issues（后续 HITL REQ）

## 测试方案

- 单元测试：budget window 计算、4 条 rule 各触发 / 不触发 / 边界值、budget/cooldown skip
- integration：tick detect-only 写 pending、tick budget 打满后 skip
- contract 测试：IMPR-S1..S9 覆盖 9 个场景
