# REQ-fixer-cap-5-to-2-1777420814: Lower fixer round cap from 5 to 2

## Problem

当前 `fixer_round_cap` 默认值为 5，意味着一个 REQ 在 verifier↔fixer 循环中最多可以走 5 轮
fix 才触发 escalate。实际运行数据表明：

1. 超过 2 轮的 fixer 循环极少带来实质性质量提升
2. 多轮循环消耗大量 token 和 wall-clock 时间
3. 3-5 轮往往是 fixer 在原地打转（lint 修完测试红、测试修完 lint 红、或同一类问题反复出现）

## Solution

将 `fixer_round_cap` 默认值从 5 降至 2：

1. `orchestrator/src/orchestrator/config.py`: `fixer_round_cap: int = 5` → `fixer_round_cap: int = 2`
2. 更新所有文档中硬编码的 cap 引用（IMPACT-REPORT.md、user-feedback-loop.md）
3. 更新测试断言，反映新默认值
4. 新增 observability SQL（Q19/Q20）跟踪 fixer round 分布与 verifier 决策变化，
   用于数据驱动验证 cap=2 是否过紧

## Scope

- `orchestrator/src/orchestrator/config.py`: 修改默认值
- `docs/IMPACT-REPORT.md`: 更新 cap 引用 5→2
- `docs/user-feedback-loop.md`: 更新 cap 引用 5→2
- `orchestrator/tests/test_contract_fixer_round_cap.py`: 更新 docstring
- `orchestrator/tests/test_verifier.py`: 更新 cap 测试（默认值 2 + monkeypatch 锁旧值保计数器测试）
- `orchestrator/tests/test_watchdog.py`: 更新注释
- `observability/queries/sisyphus/19-fixer-round-distribution.sql`: 新增
- `observability/queries/sisyphus/20-fixer-decision-distribution-by-cap.sql`: 新增

## Rollback trigger

如果 cap=2 后 escalate 率上升 ≥10pp（百分点），数据将提示 cap 过紧，
应考虑调回 3。Q19/Q20 用于持续监控此指标。
