# REQ-metabase-silent-pass-detector-1777123726: feat(observability): Q18 silent-pass detector for mechanical checkers

## 问题

`spec_lint` / `dev_cross_check` / `staging_test` / `pr_ci_watch` 各自源代码里都
显式写了 `refusing to silent-pass` guard：

- `orchestrator/src/orchestrator/checkers/spec_lint.py:42-78` — `/workspace/source` 不存在 / 没子目录 / `ran=0` 都直接 `exit 1`
- `orchestrator/src/orchestrator/checkers/dev_cross_check.py:38-75` — 同结构
- `orchestrator/src/orchestrator/checkers/staging_test.py:48-87` — 同结构
- `orchestrator/src/orchestrator/checkers/pr_ci_watch.py:329-365` — `_classify` 在全绿但 0 条 GHA check-run 时返 `no-gha`，调度方按 fail 分支处理（`no-gha-checks-ran` 上 stdout）

设计上"零信号即 fail"，但**只有源码层**这一道防线。一旦：

1. checker 脚本里某行 `[ "$ran" -eq 0 ]` guard 被改坏（或 `[` 语法手误，bash 退出码不是 1 而是 0）
2. `_classify` 里被加进新 conclusion 但忘了归到 fail 分支
3. for 循环静默吃掉错误（如 `git fetch ...; ...; (cd $repo && ... ) || true` 之类的修改）

→ checker 报 `passed=true`（`exit_code=0`）但**实际啥也没跑**。这种"沉默通过"
状态机会以为本仓全绿继续推下一 stage，最坏情况合掉一个根本没过测试的 PR。

`artifact_checks` 表里其实**有**这种 silent-pass 的痕迹：

- 一旦 guard 被绕过但 `=== FAIL spec_lint: ... refusing to silent-pass ===` 这种 stderr 留在了 `stderr_tail` 里 → 痕迹
- pr_ci_watch 在 stdout_tail 留 `no-gha-checks-ran` 但 passed=true → 痕迹
- duration_sec 异常短（远低于同 stage P50）= 脚本短路 → 痕迹

但当前看板（Q1–Q16）**没有任何一条 SQL** 把这类样本捞出来 —— Q1 看 fail_count，
Q2 看慢异常，Q3 看通过率，都是 `passed=true` 直接信任的；Q14–Q16 看 fixer audit
（agent 主观作弊），不是机械层 silent-pass。

## 根因

机械 checker 的 silent-pass 现在是"靠源码 review 兜底"，没有 metrics 层
defense-in-depth。文档 [docs/integration-contracts.md](../../../docs/integration-contracts.md)
和 [docs/architecture.md](../../../docs/architecture.md) 反复强调"零信号即 fail" 是
sisyphus 哲学之一，但没有看板帮人**反查源码是否还在这条哲学之内**。

## 方案

补一条 SQL（Q18）+ 一段 [observability/sisyphus-dashboard.md](../../../observability/sisyphus-dashboard.md)
说明，从 `artifact_checks` 已落表样本反查"通过了但其实没干活"的记录。

不动 checker 源码，不加新表 / 列，不改 schema / migration。**纯观测增量**。

### 三类信号（CASE 优先级从严到松）

| 信号 | 触发条件（passed=true 前提下） | 含义 |
|---|---|---|
| `guard-leak` | `stdout_tail`+`stderr_tail` 命中 `refusing to silent-pass` | guard 行打了但 exit code 还是 0 → checker 实现 bug |
| `no-gha-pass` | `stdout_tail` 命中 `no-gha-checks-ran` | `_classify` 走到 no-gha 分支但 passed=true → pr_ci_watch 分类逻辑被改坏 |
| `too-fast` | `duration_sec < 0.2 × 同 stage 7d P50（passed=true 样本）` | 跑得比中位数快 5×，多半是 for 循环没进 body |

`guard-leak` / `no-gha-pass` 是绝对信号（不依赖基线），任意命中即立即介入。
`too-fast` 是统计异常，需要同 stage 7d passed=true 样本 ≥ 20 才计算（避免小样本噪声，
和 Q2 慢异常一致）。

### 文件落点

- 新增 `observability/queries/sisyphus/18-silent-pass-detector.sql`
- 在 `observability/sisyphus-dashboard.md` 加一节"机械 checker silent-pass（Q18）"，
  跟"Fixer Audit（Q14–Q16）"并列，说明三类信号 + Visualization + 阈值
- 看板布局 + 刷新频率 + 告警章节同步追加 Q18 行

### Verify

- `openspec validate openspec/changes/REQ-metabase-silent-pass-detector-1777123726 --strict` 通过
- `psql -d sisyphus -f observability/queries/sisyphus/18-silent-pass-detector.sql` 在干净库上 SQL 解析通过（语法 / 列名 / 类型）
- 在生产 metabase 接入 Postgres 数据源后**手动**跑一次 SQL → 应当 0 行（生产没 silent-pass）
- `make ci-lint && make ci-unit-test` 在 self-dogfood 下不打破（零 Python 改动只须不引入 lint）

## 取舍

- **为什么 Q 编号是 18 不是 14（REQ 名里的标签）** —— Q14 已经被
  [Fixer audit verdict trend](../../../observability/queries/sisyphus/14-fixer-audit-verdict-trend.sql)
  占了（migration 0006 之后引入），后续 Q15 / Q16 / Q17 也都已存在文件。
  REQ 命名时 user 不知道这个冲突，实际下一个可用槽是 Q18。改 REQ 名会动 BKD issue
  和 git ref，不值得；只在 proposal / dashboard md 里清楚说明 "REQ 名里的 Q14
  对应实际 Q18"即可。文件名（`18-silent-pass-detector.sql`）跟 dashboard md 章节
  标题（"Q18. Silent-pass detector"）保持一致。
- **为什么不在 checker 源码里加机械防御** —— 已经有 `[ "$ran" -eq 0 ] && exit 1`
  + `set -o pipefail` 这套；本 REQ 是 metrics 层兜底，跟源码层 guard 是 defense-in-depth
  的**两道**而不是替代。改源码再加一层意义不大，反而 metrics 兜底能 catch "源码本身被改坏"
  的场景（最危险的那种）。
- **为什么不针对 `stage_runs` 也写一份** —— `stage_runs` 是 agent 侧（埋点尚未全覆盖），
  本 REQ 只关注**机械 checker** 的 silent-pass（artifact_checks 已是稳态全覆盖）。
  agent 侧 silent-pass（agent 报 pass 但没真做事）由 verifier audit Q15 已经覆盖。
- **为什么 too-fast 阈值用 P50 × 0.2 而不是 P50 × 0.1 或绝对秒数** —— 0.2 给 5 倍的
  容差区，足够 catch "for 循环没进 body" 这种数量级偏差，又不会被同 stage 自然 variance
  误报。绝对秒数无法兼容 spec_lint（基线 1–3s）和 staging_test（基线 几分钟）两种
  数量级。和 Q2（慢异常）的 `× 2` 是对称设计。
- **为什么 too-fast 要求样本 ≥ 20** —— 同 Q2，小样本 P50 噪声大，stage 刚开始用没历史
  时直接 silent-pass-kind=NULL 跳过比误报安全。`guard-leak` / `no-gha-pass` 不依赖
  基线，无样本要求。
- **为什么不同时落 Metabase Question JSON / dashboard JSON** —— 跟 Q1–Q17 一致，本 repo
  只维护 SQL + md；Metabase Question 由人工导入（dashboard.md §"数据源"段已说明）。
  导出 JSON 增加 schema 维护负担，目前 sisyphus 没自动化 metabase migration。
