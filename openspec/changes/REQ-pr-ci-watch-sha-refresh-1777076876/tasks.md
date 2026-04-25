# Tasks for REQ-pr-ci-watch-sha-refresh-1777076876

## Stage: contract / spec

- [x] author specs/pr-ci-watch-sha-refresh/spec.md — SHA 翻转检测、flip 限制、PR merged/closed 处理、refetch retry 语义

## Stage: implementation

- [x] 修改 `_get_pr_info` 返回 3-tuple `(pr_number, sha, state)` 支持 open/merged/closed 区分
- [x] 添加 `_RepoState` dataclass 跟踪 per-repo sha、flip_count、terminal_verdict
- [x] 重构 `watch_pr_ci` 主循环：每 tick 重新拉 PR info，检测 SHA 翻转 + PR 状态变化
- [x] SHA 翻转：log sha_flip、flip_count 超 5 → terminal fail reason=too-many-sha-flips
- [x] PR merged → terminal pass；PR closed → terminal fail reason=pr-closed-without-merge
- [x] refetch 失败（HTTP/ValueError）→ 警告 + retry，不立即 fail
- [x] 删除原有 `cmd_label` 静态字符串，改为 closure 函数确保 SHA 更新后 cmd 同步

## Stage: unit test

- [x] 更新 `patch_pr_lookup` 及所有 `fake_lookup` mock 返回 3-tuple（兼容新签名）
- [x] 更新 `test_watch_pr_ci_per_req_repos_override_env`：assertion 改为 `set(looked_up)` 覆盖多次调用
- [x] 新增 `test_watch_pr_ci_sha_flip_restarts_check_runs`：force-push 后从新 SHA 轮询
- [x] 新增 `test_watch_pr_ci_too_many_sha_flips`：超 5 次翻转 → fail
- [x] 新增 `test_watch_pr_ci_pr_merged_returns_pass`：loop 中 PR 被 merge → 立即 pass
- [x] 新增 `test_watch_pr_ci_pr_closed_returns_fail`：loop 中 PR 被 close → fail
- [x] 新增 `test_watch_pr_ci_initial_pr_already_merged`：初始 fetch 发现已 merge → 立即 pass
- [x] 新增 `test_watch_pr_ci_pr_refetch_error_retries`：loop 内 refetch HTTP 失败 → 重试

## Stage: PR

- [x] git push feat/REQ-pr-ci-watch-sha-refresh-1777076876
- [x] gh pr create
