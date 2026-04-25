## MODIFIED Requirements

### Requirement: pr_ci_watch 每 tick 重新拉 head SHA 以检测 force-push

The `watch_pr_ci` checker SHALL re-fetch the PR head SHA on every polling tick (not just once at startup). When the fetched SHA differs from the cached SHA, the system MUST clear the check-runs cache for that repo and restart polling against the new SHA. The system MUST log a `sha_flip` event at INFO level with fields `old`, `new`, and `flip_count` each time a SHA change is detected.

#### Scenario: PRCISHAR-S1 force-push 后切换到新 SHA 继续轮询

- **GIVEN** PR 初始 head SHA 为 sha_A，sha_A 的 check-runs pending
- **WHEN** 下一 tick 重新拉 PR info 返回 sha_B（force-push）
- **THEN** sha_A 的 check-runs 缓存被清除，系统从 sha_B 的 check-runs 继续轮询

#### Scenario: PRCISHAR-S2 新 SHA 的 check-runs 最终通过 → overall pass

- **GIVEN** force-push 后切换到 sha_B（flip_count=1 ≤ 5）
- **WHEN** sha_B 的所有 check-runs completed 且全部 conclusion 为绿色
- **THEN** `watch_pr_ci` 返回 passed=True, exit_code=0

### Requirement: SHA 翻转超过 5 次时 fail with too-many-sha-flips

The system SHALL track SHA flip count per repo. If the flip count exceeds 5 (i.e., the 6th force-push is detected), the system MUST set `terminal_verdict="fail"` for that repo and return a `CheckResult` with `passed=False`, `exit_code=1`, and `stdout_tail` containing the string `too-many-sha-flips`. Each repo MUST track its flip count independently (per-repo restart semantics).

#### Scenario: PRCISHAR-S3 翻转 5 次以内继续轮询

- **GIVEN** 单个 repo 已发生 5 次 SHA 翻转（flip_count=5）
- **WHEN** 拉到第 5 次新 SHA
- **THEN** 继续正常轮询，不触发 too-many-sha-flips

#### Scenario: PRCISHAR-S4 翻转第 6 次 → fail too-many-sha-flips

- **GIVEN** 单个 repo flip_count 已为 5
- **WHEN** 检测到第 6 次 SHA 变化（flip_count 变为 6）
- **THEN** `watch_pr_ci` 返回 passed=False, exit_code=1，stdout_tail 包含 `too-many-sha-flips`

### Requirement: PR merged 时立即 pass，closed 时立即 fail

The system SHALL detect PR state changes on every polling tick. When `_get_pr_info` returns state `"merged"`, the system MUST set the terminal verdict for that repo to `"pass"` without waiting for check-runs. When `_get_pr_info` returns state `"closed"` (closed without merge), the system MUST set the terminal verdict to `"fail"` with reason `pr-closed-without-merge`. If all repos reach terminal `"pass"`, `watch_pr_ci` MUST return immediately with `passed=True`.

#### Scenario: PRCISHAR-S5 PR merged 后 watch_pr_ci 立即返 pass

- **GIVEN** PR 在 loop 某 tick 被 merge（`merged_at` 非 null）
- **WHEN** re-fetch 返回 state="merged"
- **THEN** `watch_pr_ci` 返回 passed=True，stdout_tail 含 "merged"，不再拉 check-runs

#### Scenario: PRCISHAR-S6 PR closed（未 merge）→ fail pr-closed-without-merge

- **GIVEN** PR 在 loop 某 tick 被关闭但未 merge（`merged_at` 为 null）
- **WHEN** re-fetch 返回 state="closed"
- **THEN** `watch_pr_ci` 返回 passed=False, exit_code=1，stdout_tail 含 `pr-closed-without-merge`

### Requirement: PR info refetch 失败时 retry 直到 deadline

The system SHALL treat PR info refetch failures (both `httpx.HTTPError` and `ValueError` from "no PR found") during the polling loop as transient errors. The system MUST log a WARNING and continue to the next tick without immediately returning a failure. This behavior MUST be consistent with how check-run API errors are handled. Only the initial PR fetch (before the loop) SHALL fail fast on errors.

#### Scenario: PRCISHAR-S7 loop 内 refetch HTTP 错误 → 警告并重试

- **GIVEN** `_get_pr_info` 在第 N 个 tick 抛出 `httpx.HTTPError`（下一个 tick 恢复正常）
- **WHEN** `watch_pr_ci` 捕获到该错误
- **THEN** 记录 WARNING 日志，使用缓存的 SHA 继续拉 check-runs，不返回 fail；下一 tick 恢复正常拉取
