# Tasks for REQ-pr-ci-no-gha-1777105576

## Stage: contract / spec

- [x] author specs/pr-ci-no-gha/spec.md —— `no-gha` verdict 定义、GHA 识别规则、与 pending/fail 的优先级、`stdout_tail` 格式

## Stage: implementation

- [x] `pr_ci_watch.py`：新增模块常量 `_GHA_APP_SLUG = "github-actions"`
- [x] `_classify`：返回值新增 `'no-gha'`；遍历 runs 时跟踪 `has_gha`；全绿无 pending 时若 `has_gha=False` 返 `'no-gha'`，否则原 `'pass'`
- [x] `_classify`：`has_pending` 仍优先于 `no-gha`（pending 等 GHA 上车）
- [x] `_classify`：缺 `app` 字段保守按非 GHA 处理（不能撑 pass）
- [x] `watch_pr_ci`：把 `no-gha` 与 `fail` 同等当失败，构造 `stdout_tail` 形如 `<repo>: no-gha-checks-ran (only non-CI signals: ...)` 暴露实际跑了啥

## Stage: unit test

- [x] `test_classify_all_green_but_only_review_only_check_run_is_no_gha` —— 全绿但只有 `anthropic-claude` review bot → `no-gha`
- [x] `test_classify_mixed_gha_plus_review_is_pass` —— 一条 GHA + 一条 review-only 全绿 → `pass`
- [x] `test_classify_pending_overrides_no_gha` —— 有 pending 不当下断 `no-gha`
- [x] `test_classify_check_run_missing_app_field_treated_as_non_gha` —— 缺 `app` 字段全绿 → `no-gha`
- [x] `test_watch_pr_ci_review_only_check_runs_treated_as_fail` —— 端到端：PR 只有 claude-review → `passed=False, exit_code=1, stdout_tail` 含 `no-gha-checks-ran`
- [x] 更新现有 `_run` fixture 默认 `app.slug="github-actions"` 兼容老测试
- [x] 更新 `test_contract_pr_ci_watch_sha_refresh.py` 的 `_pending_cr` / `_success_cr` 给 check-run 加上 `"app": {"slug": "github-actions"}`

## Stage: PR

- [x] git push feat/REQ-pr-ci-no-gha-1777105576
- [x] gh pr create
