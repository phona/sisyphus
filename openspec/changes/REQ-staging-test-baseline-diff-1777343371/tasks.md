# Tasks for REQ-staging-test-baseline-diff-1777343371

## Stage: contract / spec

- [x] author `specs/staging-test-baseline-diff/spec.md` ADDED delta:
      two-phase baseline diff for staging_test checker
- [x] 列 4 条 scenario（BD-1..BD-4）覆盖 pass/diff-empty/new-fail/baseline-exception

## Stage: implementation

- [x] `orchestrator/migrations/0011_baseline_results.sql` + rollback：
      新增 `baseline_results` 表（cache_key UNIQUE, main_sha, repo_results JSONB, created_at）
- [x] `orchestrator/src/orchestrator/store/baseline_results.py`：
      `get_cached(pool, cache_key) -> dict[str, bool] | None` +
      `put_cached(pool, cache_key, main_sha, repo_results)` 两函数
- [x] `orchestrator/src/orchestrator/checkers/staging_test.py`：
  - `_build_get_main_sha_cmd()` — 快速取 origin/main HEAD SHA
  - `_build_baseline_cmd()` — checkout origin/main，跑同套 ci-*，
    emit PASS/FAIL 标记 + MAIN_SHA；不要求 feat/<REQ> 分支
  - `_parse_repo_results(stdout, stderr) -> dict[str, bool]` — 解析 PASS/FAIL 标记
  - `_parse_main_sha(stdout) -> str | None`
  - `_compute_diff(baseline, pr_repos) -> (baseline_failures, pr_failures, introduced)`
  - `_format_diff_header(...)` — 生成 SISYPHUS BASELINE DIFF 上下文块
  - `run_staging_test(req_id)` 改为三阶段：SHA get → baseline（24h 缓存）→ PR run → diff
  - baseline 阶段异常 → 退化到老逻辑
- [x] `orchestrator/src/orchestrator/actions/create_staging_test.py`：
      fail 路径追加 `req_state.update_context(pool, req_id, {"staging_test_stderr_tail": result.stderr_tail})`
- [x] `orchestrator/src/orchestrator/actions/_verifier.py`：
      `invoke_verifier_for_staging_test_fail` 从 `ctx.staging_test_stderr_tail` 读
      stderr_tail 并透传给 `invoke_verifier`
- [x] `orchestrator/src/orchestrator/prompts/verifier/staging_test_fail.md.j2`：
      加"Baseline 差量分析"指引节：SISYPHUS BASELINE DIFF 块存在时优先按差量判，
      `pr_introduced_failures: []` → 判 pass

## Stage: unit test

- [x] `orchestrator/tests/test_checkers_staging_test.py`：
  - BD-1: baseline 全 pass + PR 全 pass → staging-test.pass（老逻辑走）
  - BD-2: baseline N fail + PR 同 N fail → diff 空 → staging-test.pass，stdout 含 BASELINE DIFF
  - BD-3: baseline N fail + PR N+1 fail → staging-test.fail，stderr 含 pr_introduced_failures
  - BD-4: baseline SHA get 异常 → 退化老逻辑，PR exit_code=1 → fail，无 BASELINE DIFF 块
- [x] 已有测试（pass / fail / truncate / timeout / build_cmd / flake-retry）全部调整兼容：
      `_make_seq_controller` 测试在序列头部追加 SHA-get 空结果（skip baseline）

## Stage: PR

- [x] git push feat/REQ-staging-test-baseline-diff-1777343371
- [x] gh pr create --label sisyphus
