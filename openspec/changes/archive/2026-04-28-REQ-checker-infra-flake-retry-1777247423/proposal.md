# REQ-checker-infra-flake-retry-1777247423: feat(checkers): bounded retry on pattern-matched infra flakes

## 问题

三个 kubectl-exec checker（`spec_lint` / `dev_cross_check` / `staging_test`）一次跑挂就直接
emit `<stage>.fail`：

```
exec_in_runner → CheckResult(passed=False, exit_code=1, …)
                ↓
        emit <stage>.FAIL
                ↓
        REVIEW_RUNNING → start_verifier_<stage>
                ↓
        verifier-agent 主观判 pass / fix / escalate
```

但 checker 命令的失败原因有两种结构性不同：

1. **真业务失败**：测试 / lint / openspec validate 真的报错 —— 跟 agent 写的代码 / spec
   有关。verifier 主观判最合适。
2. **基础设施抖动**（"infra flake"）—— 跟 agent 输出无关、跟代码无关、几秒后再跑就过：
   - GHA / GitHub API 5xx
   - GHCR / Docker Hub registry rate-limit (`TOOMANYREQUESTS`) / TLS handshake timeout
   - DNS resolve 短暂失败 (`Could not resolve host`, `Temporary failure in name resolution`)
   - kubectl exec channel race (`error: unable to upgrade connection`, `error dialing backend`)
   - go mod / npm 下载 5xx / TCP reset
   - `git fetch origin feat/<REQ>` 偶发 RPC HTTP 5xx

把 (2) 喂给 verifier 是浪费 token：verifier 看到 `Could not resolve host` 99.9% 会判
`escalate`（CLAUDE.md `docs/architecture.md §7` 已写明"基础设施 flaky / 外部抖动 →
escalate，sisyphus 不机制性兜 retry"）。一条 REQ 卡死等人重起，pipeline 跑不下去。

但 verifier 判 `escalate` 也不是万能：

- 实证 `dev_cross_check` 跑挂跟 `kubectl exec stream race` 高度相关 —— `k8s_runner.exec_in_runner`
  已经为**纯 stream-race 空输出**做了 1 次重试（`exec_in_runner.stream_race_retry`），但
  `kubectl exec` 真的把 SPDY stream 接通后又在中段挂掉、命令本身因为 backend 抖动 exit
  非零的 case 没覆盖。
- `gh-incident-open`（PR #122）会为每个 ESCALATED REQ 在所有 involved repos 开 GH
  事故 issue。infra-flake 走 escalate → 一次 GitHub API 短抖动可以批量产 N 条事故 noise。

## 方案

新加一层 **机械层 pattern-match retry**，定位：

```
checker 内部 cmd 跑完
        ↓
exit_code != 0 → match infra-flake 模式 (regex on stderr/stdout tail)
                        ↓
              match → 同 cmd 直接重跑 1 次（默认；可调）+ 短 backoff (默认 15 秒)
                        ↓
              重跑 pass → 整体 pass，CheckResult.reason = "flake-retry-recovered:<tag>"
              重跑仍 fail → 整体 fail，CheckResult.reason = "flake-retry-exhausted:<tag>"
                        ↓
              不 match → 立即 fail（**不重试真业务错**），reason=None（保留原有行为）
```

具体 3 条边界设计：

1. **pattern 必须明显是 infra**：保守起见只匹配会让 verifier 100% 判 escalate 的字符串
   （DNS / TLS / 5xx / kubectl exec channel error / registry rate limit）。模糊的失败
   （`exit code 137` / generic `make: *** Error 1`）**不**触发 retry —— 不能蒙混"代码挂了"
   假装 flake。
2. **bounded**：默认 `max_retries=1`（即同 cmd 重跑 1 次，最多 2 attempts），有 `max=0`
   关闭。理由：infra flake 一般几秒到几十秒就恢复；若一次重跑还挂，**很可能不是 flake**
   或者外部彻底挂了（GitHub down），让 verifier 接手判 escalate。
3. **不动 verifier**：retry 完成后 emit 的 event 仍是
   `STAGING_TEST_PASS / STAGING_TEST_FAIL`（等等），verifier 链路完全不变。`reason` 字段
   只是给 `artifact_checks` 表 + 日志多一个标签，方便看板侧后续做 "infra-flake 比例"
   指标。
4. **不动 pr_ci_watch**：`pr_ci_watch` 已经针对 GitHub HTTP error 在 watch loop 内做
   retry-until-deadline（见 `checker.pr_ci_watch.api_error` warning），跟本 REQ 的
   "kubectl exec 一次跑挂"是不同模型。pr_ci_watch 出 retry 范围。

## 影响范围

新增：

- `orchestrator/src/orchestrator/checkers/_flake.py` —— pattern 表 +
  `classify_failure()` + `run_with_flake_retry()`
- `orchestrator/migrations/0009_artifact_checks_flake.sql` (+ rollback) ——
  `artifact_checks` 加两列 `attempts INT` + `flake_reason TEXT`
- `orchestrator/tests/test_checkers_flake.py` —— 单测覆盖 pattern + retry 逻辑

修改：

- `orchestrator/src/orchestrator/checkers/_types.py` —— `CheckResult` 加
  `attempts: int = 1` 字段
- `orchestrator/src/orchestrator/checkers/spec_lint.py` ——
  改用 `run_with_flake_retry(...)` 包 `exec_in_runner`
- `orchestrator/src/orchestrator/checkers/dev_cross_check.py` —— 同上
- `orchestrator/src/orchestrator/checkers/staging_test.py` —— 同上
- `orchestrator/src/orchestrator/store/artifact_checks.py` —— 写 `attempts` +
  `flake_reason` 两列（兼容旧 row：default 1, NULL）
- `orchestrator/src/orchestrator/config.py` —— 加 3 个 settings：
  - `checker_infra_flake_retry_enabled: bool = True`
  - `checker_infra_flake_retry_max: int = 1`
  - `checker_infra_flake_retry_backoff_sec: int = 15`

不改：

- `orchestrator/src/orchestrator/checkers/pr_ci_watch.py`（已有自己的 HTTP retry 模型）
- 状态机 / event 表（`<stage>.PASS/FAIL` 语义不变；retry 全在 checker 内部）
- verifier 链路（reason 字段是 informational，verifier 不读）
- BKD agent prompts
- Metabase 看板（看板可后续用 `flake_reason` 加新查询，不在本 REQ 范围）
