# REQ-analyze-artifact-check-1777254586 — analyze 阶段 post-artifact-check

## Why

analyze BKD agent 完成 session 后会被 router 派 `Event.ANALYZE_DONE`，
`(ANALYZING, ANALYZE_DONE)` 直接转 `SPEC_LINT_RUNNING`。但 sisyphus 没机械验过
agent 是否真的写出了顶层 ✅ 清单要求的产物：

- 仅看 BKD session 状态和 `analyze` tag —— agent 可以"我做完了"声明，把 issue
  挪到 review 就触发 ANALYZE_DONE。
- `spec_lint` 是下一站，但它只跑 `openspec validate` + `check-scenario-refs.sh`，
  逻辑只关心 `openspec/changes/<REQ>/specs/<capability>/spec.md`。
  proposal.md / tasks.md 缺失或 0 字节，spec_lint 仍可能过。
- 实证：`spec_lint._build_cmd` 已经针对"0 cloned repo / 0 eligible repo"加了
  empty-source guard（REQ-checker-empty-source-1777113775），但那是兜底，不是
  对 analyze 产物的契约级检查。

只要 agent "自报 pass 但无产物"通过 sisyphus 的机械关，REQ 会一路推到 verifier
甚至 fixer 才被发现，浪费 token + 拖慢 wall-clock。

## What Changes

新增**机械 post-artifact-check** stage，夹在 ANALYZING → SPEC_LINT_RUNNING 之间：

- 加状态 `ReqState.ANALYZE_ARTIFACT_CHECKING`
- 加事件 `Event.ANALYZE_ARTIFACT_CHECK_PASS` / `Event.ANALYZE_ARTIFACT_CHECK_FAIL`
- 改 transition：
  - `(ANALYZING, ANALYZE_DONE)` → `ANALYZE_ARTIFACT_CHECKING` + `create_analyze_artifact_check`（原来直接转 SPEC_LINT_RUNNING）
  - `(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS)` → `SPEC_LINT_RUNNING` + `create_spec_lint`
  - `(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_FAIL)` → `REVIEW_RUNNING` + `invoke_verifier_for_analyze_artifact_check_fail`
- 加 SESSION_FAILED self-loop 兜 ANALYZE_ARTIFACT_CHECKING
- 新 checker `checkers/analyze_artifact_check.py` —— 在 runner pod 内遍历
  `/workspace/source/*/`，对每个有 `feat/<REQ>` 远程分支的仓校验：
  1. `openspec/changes/<REQ>/proposal.md` 存在且非空（累积，至少一仓有）
  2. `openspec/changes/<REQ>/tasks.md` 存在且非空，**至少含一个 `- [ ]` 或 `- [x]` 复选框**（累积）
  3. `openspec/changes/<REQ>/specs/*/spec.md` 至少有一个非空文件（每个 eligible 仓必须有）
- 新 action `create_analyze_artifact_check` —— 调 checker、写 `artifact_checks`、
  emit pass/fail（与 `create_spec_lint` 同形）
- 复用 `artifact_checks` 表，stage 列写 `analyze-artifact-check`
- verifier 接入：在 `_verifier.py` 注册 `analyze_artifact_check` 作为合法 stage、
  加 `_PASS_ROUTING` 条目（pass → 回 ANALYZE_ARTIFACT_CHECKING + emit PASS）、
  注册 `invoke_verifier_for_analyze_artifact_check_fail` action handler；新增
  `prompts/verifier/analyze_artifact_check_{success,fail}.md.j2` 模板

## Impact

- **架构 checker 数从 4 加到 5**（原 spec_lint / dev_cross_check / staging_test /
  pr_ci_watch）。接口形状对齐，`artifact_checks` 表 / 看板 SQL 不需结构变更
- **状态机 transition 数 +5**（4 主链 + 1 SESSION_FAILED self-loop）
- **fail 路径多走一轮 verifier**；快乐路径通过率不变（只多一次轻量 shell 检查
  ~1s），代价微乎其微
- 对 docs-only / spec-only REQ 不会误伤——只要 proposal.md / tasks.md / spec.md
  非空就 pass，**不**强制要求实际业务代码 diff
- 旧 REQ 在迁移上线时不受影响（artifact_checks schema 不变；checker 找不到
  feat 分支会跳过，跟 spec_lint 一致）
