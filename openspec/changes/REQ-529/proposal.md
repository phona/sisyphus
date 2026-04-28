# REQ-529: 补齐状态机 transition 单测（21% → 80%+）

## 问题

IMPACT-REPORT.md 自认核心 transition 仅覆盖 9/42 条（约 21%）。17 状态 × 27 事件 × 30+ transition 的复杂状态机缺乏兜底测试。

## 方案

1. 梳理 `state.py` 中所有 transition，建立未覆盖清单
2. 为未覆盖 transition 编写增量 mock 测试（不碰已有测试）
3. 重点覆盖：异常路径、竞态路径、verifier 3 路决策后的分支
4. 新增 `test_state_transitions_gap.py`，与现有 `test_state.py` 互补

## 验收标准

- transition 覆盖率 ≥ 80%
- ci-lint 通过
- dev_cross_check 通过（状态机相关测试全绿）
