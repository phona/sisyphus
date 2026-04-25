# Tasks: REQ-metabase-silent-pass-detector-1777123726

## Stage: spec

- [x] `proposal.md` 详述根因 + 方案 + 三类信号定义 + 取舍
- [x] `specs/silent-pass-detector/spec.md`（ADDED Requirements + Scenarios，覆盖三类信号 + 基线 + dashboard 说明）
- [x] `tasks.md`（本文件，每个 checkbox 反映真实交付状态）

## Stage: implementation

- [x] 新增 `observability/queries/sisyphus/18-silent-pass-detector.sql` — 三类信号 CASE + 7d P50 baseline CTE + 24h 窗口
- [x] `observability/sisyphus-dashboard.md` 加 "机械 checker silent-pass（Q18）" 节，含 Visualization / 三类信号说明 / 阈值
- [x] `observability/sisyphus-dashboard.md` 看板布局段补 Q18 框图
- [x] `observability/sisyphus-dashboard.md` 刷新频率段加 Q18 行（每 5 分钟，同 Q2 节奏）
- [x] `observability/sisyphus-dashboard.md` 告警段加 Q18 alert 触发条件（`guard-leak` / `no-gha-pass` 行 → Lark）
- [x] `observability/sisyphus-dashboard.md` cache TTL 行把 Q18 加入 120s 组（同 Q2/Q3/Q4/Q12/Q13）

## Stage: verify

- [x] `openspec validate openspec/changes/REQ-metabase-silent-pass-detector-1777123726 --strict` 通过
- [x] `grep -n 'refusing to silent-pass\|no-gha-checks-ran' observability/queries/sisyphus/18-silent-pass-detector.sql` 命中 ≥ 2 处（信号字符串与 checker 源码字面对齐）
- [x] `grep -n 'PERCENTILE_CONT(0.5)' observability/queries/sisyphus/18-silent-pass-detector.sql` 命中（baseline CTE 用 P50）
- [x] `grep -nE 'COUNT\(\*\) >= 20' observability/queries/sisyphus/18-silent-pass-detector.sql` 命中（20 sample threshold）
- [x] `grep -n 'Q18' observability/sisyphus-dashboard.md` ≥ 5 处命中（章节 + 布局 + 刷新 + 告警 + cache）
- [x] `make ci-lint` 不报新 lint 错误（零 Python 改动只须不打破）

## Stage: PR

- [x] commit `feat(observability): Q18 silent-pass detector for mechanical checkers (REQ-metabase-silent-pass-detector-1777123726)`
- [x] push origin feat/REQ-metabase-silent-pass-detector-1777123726
- [x] gh pr create
