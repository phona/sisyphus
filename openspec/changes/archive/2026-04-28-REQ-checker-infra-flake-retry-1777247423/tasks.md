# Tasks for REQ-checker-infra-flake-retry-1777247423

## Stage: contract / spec

- [x] author `specs/checker-infra-flake-retry/spec.md` ADDED delta:
      pattern-match + bounded retry across 3 kubectl-exec checkers + reason / attempts
      record on `artifact_checks`
- [x] 列 12 条 scenario（CIFR-S1..S12）覆盖 pattern 命中 / 不命中 / 重试恢复 /
      重试耗尽 / pr_ci_watch 不变 / config 关闭 / 真业务 fail 不被误吞
- [x] 显式说明 `reason` 用 `flake-retry-recovered:<tag>` /
      `flake-retry-exhausted:<tag>` 两种字符串值，及 `attempts` 字段从 1 起算

## Stage: implementation

- [x] `orchestrator/src/orchestrator/checkers/_types.py`：`CheckResult` 加
      `attempts: int = 1` 字段（兼容旧构造点，不传 = 1）
- [x] 新增 `orchestrator/src/orchestrator/checkers/_flake.py`：
  - `INFRA_FLAKE_PATTERNS: list[tuple[re.Pattern[str], str]]`（DNS / kubectl-exec /
    github-fetch / registry-rate-limit / registry-network / go-mod / npm-network /
    apt-mirror 共 8 类）
  - `classify_failure(stdout_tail, stderr_tail, exit_code) -> str | None`
  - `run_with_flake_retry(*, coro_factory, stage, req_id, max_retries, backoff_sec) -> tuple[ExecResult, int, str | None]`
- [x] `orchestrator/src/orchestrator/checkers/spec_lint.py`：把直接 `await
      asyncio.wait_for(rc.exec_in_runner(...))` 改为 `run_with_flake_retry(coro_factory=...)`
      + 把 `(attempts, flake_reason)` 灌进 CheckResult
- [x] `orchestrator/src/orchestrator/checkers/dev_cross_check.py`：同上结构调整
- [x] `orchestrator/src/orchestrator/checkers/staging_test.py`：同上结构调整
- [x] `orchestrator/src/orchestrator/config.py`：加
      `checker_infra_flake_retry_enabled: bool = True` /
      `checker_infra_flake_retry_max: int = 1` /
      `checker_infra_flake_retry_backoff_sec: int = 15`
- [x] 新增 `orchestrator/migrations/0009_artifact_checks_flake.sql` +
      `0009_artifact_checks_flake.rollback.sql`：`attempts INT DEFAULT 1` +
      `flake_reason TEXT NULL` + 偏置 partial index
- [x] `orchestrator/src/orchestrator/store/artifact_checks.py`：写新两列
      （`attempts` / `flake_reason`）

## Stage: unit test

- [x] 新增 `orchestrator/tests/test_checkers_flake.py`：
  - CIFR-S1：`classify_failure` 对每个 pattern 的代表性 stderr → 正确 tag
  - CIFR-S2：非 flake stderr / 业务错（`make: *** Error 1`）→ None
  - CIFR-S3：exit_code=0 → None（不能在 pass 上挂 retry 标签）
  - CIFR-S4：`run_with_flake_retry` 一次 pass → attempts=1 reason=None
  - CIFR-S5：一次 non-flake fail → attempts=1 reason=None（不重试）
  - CIFR-S6：一次 flake fail → 二次 pass → attempts=2 reason="flake-retry-recovered:<tag>"
  - CIFR-S7：两次都 flake fail → attempts=2 reason="flake-retry-exhausted:<tag>"
  - CIFR-S8：max_retries=0 → 不重试，flake fail 也直接返 attempts=1 reason=None
  - CIFR-S9：backoff_sec=0 → 不 sleep（mock asyncio.sleep 不被调）
- [x] `orchestrator/tests/test_checkers_dev_cross_check.py`：加 1 条 case
      mock RC 第一次返 flake stderr exit=1、第二次 exit=0 → result.passed=True,
      attempts=2, reason 含 "flake-retry-recovered"
- [x] `orchestrator/tests/test_checkers_staging_test.py`：同上结构
- [x] `orchestrator/tests/test_checkers_spec_lint.py`：同上结构
- [x] 已有 pass / fail / timeout / build_cmd 测试不破

## Stage: PR

- [x] git push feat/REQ-checker-infra-flake-retry-1777247423
- [x] gh pr create --label sisyphus
