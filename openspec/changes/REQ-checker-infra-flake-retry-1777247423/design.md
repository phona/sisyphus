# Design: checker infra-flake retry+pattern-match

## 模块划分

```
checkers/
├── _types.py              ← CheckResult 加 attempts: int
├── _flake.py              ← NEW: 模式表 + classify_failure + run_with_flake_retry
├── spec_lint.py           ← MODIFIED: 用 run_with_flake_retry 包 exec_in_runner
├── dev_cross_check.py     ← MODIFIED: 同上
├── staging_test.py        ← MODIFIED: 同上
└── pr_ci_watch.py         ← UNCHANGED: 自有 HTTP retry，模型不同
```

## `_flake.py` 公开 API

```python
INFRA_FLAKE_PATTERNS: list[tuple[re.Pattern, str]]
    # (regex, reason_tag) — reason_tag 是稳定短词（dns / tls / registry-rate-limit / …），
    # artifact_checks.flake_reason 直接落这个字符串，给后续看板分类用。

def classify_failure(stdout_tail: str, stderr_tail: str, exit_code: int) -> str | None:
    # 返 reason_tag（matched）或 None（unmatched / 真业务失败）。
    # exit_code == 0 → 总返 None（不能在 pass 上挂 retry 标签）。
    # 优先匹 stderr_tail，再匹 stdout_tail（k8s exec channel race 出的错通常在 stderr）。

async def run_with_flake_retry(
    *,
    coro_factory: Callable[[], Awaitable[ExecResult]],
    stage: str,            # 用于结构化日志
    req_id: str,
    max_retries: int,
    backoff_sec: float,
) -> tuple[ExecResult, int, str | None]:
    # 返 (final_exec_result, attempts, flake_reason)
    # attempts ≥ 1，flake_reason 仅在发生 retry 时非 None。
    # 行为：
    #   1. 跑 coro_factory()
    #   2. exec.exit_code == 0 → 返 (exec, 1, None)
    #   3. classify_failure → None → 不 retry，返 (exec, 1, None)
    #   4. matched → log "checker.flake.match" + sleep backoff_sec + retry，
    #      重复直到 max_retries 用完。每次都 re-classify（防外部恢复后又挂另一种）。
    #   5. 用完 retries：
    #      - 最后一次 exit_code == 0 → 返 (last_exec, attempts, "flake-retry-recovered:<tag>")
    #      - 最后一次 exit_code != 0 → 返 (last_exec, attempts, "flake-retry-exhausted:<tag>")
```

## pattern 表（保守，可扩）

```python
INFRA_FLAKE_PATTERNS = [
    # ── DNS ───────────────────────────────────────────────
    (re.compile(r"Could not resolve host", re.I), "dns"),
    (re.compile(r"Temporary failure in name resolution", re.I), "dns"),
    (re.compile(r"name or service not known", re.I), "dns"),

    # ── kubectl exec / SPDY channel ────────────────────────
    (re.compile(r"error: unable to upgrade connection"), "kubectl-exec-channel"),
    (re.compile(r"error dialing backend"), "kubectl-exec-channel"),
    (re.compile(r"Error from server: error dialing"), "kubectl-exec-channel"),

    # ── git fetch / GitHub API 5xx / GHA 抖动 ──────────────
    (re.compile(r"RPC failed; (curl 56|HTTP 5\d\d)"), "github-rpc"),
    (re.compile(r"fatal: unable to access .*: (Could not resolve host|Connection (?:reset|refused|timed out)|gnutls_handshake|server certificate verification)"), "github-fetch"),
    (re.compile(r"remote end hung up unexpectedly"), "github-fetch"),
    (re.compile(r"early EOF"), "github-fetch"),
    (re.compile(r"GH001: Large files detected"), "github-fetch"),

    # ── container registry / docker pull ───────────────────
    (re.compile(r"TOOMANYREQUESTS|toomanyrequests:", re.I), "registry-rate-limit"),
    (re.compile(r"Error response from daemon: Get .*?: (dial tcp .*?(i/o timeout|connect: connection refused)|net/http: TLS handshake timeout|server gave HTTP response to HTTPS client)"), "registry-network"),
    (re.compile(r"failed to copy: httpReadSeeker: failed open: unexpected status code 5\d\d"), "registry-5xx"),

    # ── go mod / language toolchain transient ──────────────
    (re.compile(r"go: .*?: dial tcp .*?(i/o timeout|connect: connection refused)"), "go-mod"),
    (re.compile(r"go: .*?: reading .*?: 5\d\d"), "go-mod"),
    (re.compile(r"npm ERR! (network|503|ETIMEDOUT|ECONNRESET|ENOTFOUND)"), "npm-network"),

    # ── apt mirror flaky ──────────────────────────────────
    (re.compile(r"Failed to fetch http(?:s)?://.*?(Connection (?:refused|timed out)|Temporary failure resolving)"), "apt-mirror"),
]
```

设计要点：

- **不匹** generic `make: *** [Makefile:..] Error 1` —— 这是真业务失败的常见 bash echo。
- **不匹** generic `exit code 137 / 124 / -1` —— SIGKILL / timeout 多种原因，可能真测
  test hang，靠 verifier 判更安全。
- **不匹** `unauthorized: authentication required` —— 不是 flake 是 token 配错（人为修
  GH_TOKEN secret，retry 没用）。
- **不匹** `manifest unknown` / `not found` —— 一般 image tag 真不存在（人为 bug），重
  跑没用。

## retry 控制流（完整顺序图）

```
checker.run_<name>(req_id)
    │
    ├─ build_cmd(req_id)
    │
    └─ run_with_flake_retry(
           coro_factory = lambda: rc.exec_in_runner(req_id, cmd, timeout_sec=...),
           stage = "<name>",
           req_id = req_id,
           max_retries = settings.checker_infra_flake_retry_max,
           backoff_sec = settings.checker_infra_flake_retry_backoff_sec,
       )
           │
           ├─ attempt 1: exec_result = await coro_factory()
           │       │
           │       ├─ exit_code == 0 → return (result, attempts=1, reason=None)
           │       │
           │       ├─ classify(stdout, stderr, exit_code)
           │       │       │
           │       │       ├─ tag = "dns" → log + sleep + attempt 2
           │       │       │
           │       │       └─ None → return (result, attempts=1, reason=None)  ← 真业务 fail，立即返
           │       │
           │       …
           │
           └─ all retries exhausted
                   │
                   ├─ last exit_code == 0 → return (result, attempts=N+1, reason="flake-retry-recovered:tag")
                   └─ last exit_code != 0 → return (result, attempts=N+1, reason="flake-retry-exhausted:tag")
    │
    └─ CheckResult(passed=last.exit_code==0, attempts=N+1, reason=…, …)
```

## CheckResult 字段扩

```python
@dataclass(frozen=True)
class CheckResult:
    passed: bool
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_sec: float
    cmd: str
    reason: str | None = None       # 已存在；本 REQ 用 "flake-retry-recovered:<tag>" / "flake-retry-exhausted:<tag>"
    attempts: int = 1                # NEW: ≥1，发生 retry 时 ≥2
```

兼容性：所有现有 CheckResult 构造点不传 `attempts` → default 1（旧行为）。
新代码（3 个 checker）会显式传 `attempts=actual`。

## DB schema 扩 (`migrations/0009_artifact_checks_flake.sql`)

```sql
ALTER TABLE artifact_checks
    ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS flake_reason TEXT NULL;

-- 给后续看板加索引（按 reason 聚合）
CREATE INDEX IF NOT EXISTS idx_artifact_checks_flake_reason
    ON artifact_checks(flake_reason)
    WHERE flake_reason IS NOT NULL;
```

rollback：

```sql
DROP INDEX IF EXISTS idx_artifact_checks_flake_reason;
ALTER TABLE artifact_checks
    DROP COLUMN IF EXISTS flake_reason,
    DROP COLUMN IF EXISTS attempts;
```

## 配置项 (`config.py`)

```python
# ─── REQ-checker-infra-flake-retry-1777247423 ─────────────
# 三个 kubectl-exec checker（spec_lint / dev_cross_check / staging_test）的命令一次跑挂，
# 跟 pattern 表（_flake.py）匹上即视为 infra flake，同 cmd 重跑 max 次（含原跑共 max+1
# 次 attempt），中间隔 backoff_sec。enabled=False = 关闭整套，行为退回 single-shot。
# pr_ci_watch 不走这套（它有自己的 HTTP retry-until-deadline）。
checker_infra_flake_retry_enabled: bool = True
checker_infra_flake_retry_max: int = 1            # 0 = no retry, 1 = 1 retry (2 attempts)
checker_infra_flake_retry_backoff_sec: int = 15
```

## artifact_checks 写入扩 (`store/artifact_checks.py`)

```python
async def insert_check(pool, req_id, stage, result: CheckResult) -> None:
    await pool.execute(
        """
        INSERT INTO artifact_checks
            (req_id, stage, passed, exit_code, cmd, stdout_tail, stderr_tail,
             duration_sec, attempts, flake_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        req_id, stage, result.passed, result.exit_code, result.cmd,
        result.stdout_tail, result.stderr_tail, result.duration_sec,
        result.attempts, result.reason,
    )
```

## 风险 & 取舍

1. **重复 docker compose up**：`staging_test` 在重跑时会再 `make ci-integration-test`，
   而 ci-integration-test 内部 `docker compose up` 没收回上次状态可能撞名。
   **取舍**：业务仓 Makefile 契约就是 ci-* 必须幂等（docs/integration-contracts.md），
   一次 PR 里 dev 自己也会重跑。如果某个仓不幂等，那是它 Makefile 的 bug，本 REQ 不兜。
2. **flake 标签泛化漏**：模式表是字符串 regex，新出现的 infra 错（比如下个月 K8s 升级
   出新 error string）漏进真业务 fail。
   **取舍**：保守优先 —— 漏到 verifier 比误吞业务错代价小（误吞 → 用户看到测试 pass
   但实际代码 broken；漏到 verifier → verifier 多花 30s 也判 escalate，cost 可承受）。
   pattern 表显式列在 `_flake.py`，加新模式容易（一行）。
3. **重跑后 reason 跟 stderr_tail 错位**：若第一次 stderr 是 flake 模式，第二次是真业务
   错，`reason="flake-retry-exhausted:dns"` 但 `stderr_tail` 是 "test failed" —— 看似
   错位。
   **取舍**：reason 标记的是 *分类*，stderr_tail 是 *最后一次实际输出*；这是设计意图，
   verifier 优先看 stderr_tail 判，reason 是 metadata。文档里说清这一点。
4. **cap=1 不够用**：某些抖动持续几分钟（GH 大事故），1 次 retry 也救不了。
   **取舍**：本 REQ 范围内 cap=1 是合理 default。需要更多重试时通过 settings 调高
   （`SISYPHUS_CHECKER_INFRA_FLAKE_RETRY_MAX=3`），但**不**在 checker 里硬编码大数 ——
   长时间外部抖动应当 escalate 给人，不是在 checker 里堆 retry。

## 测试边界

unit test (`test_checkers_flake.py`)：

- `classify_failure` 对每个 pattern 的代表性输入 → tag；非 flake 输入 → None；exit_code=0
  → None
- `run_with_flake_retry` 行为矩阵：
  - 一次 pass → attempts=1, reason=None
  - 一次 non-flake fail → attempts=1, reason=None（不重试）
  - 一次 flake fail → 二次 pass → attempts=2, reason="flake-retry-recovered:<tag>"
  - 两次都 flake fail → attempts=2, reason="flake-retry-exhausted:<tag>"
  - max=0 → 不重试，attempts=1, reason=None
  - 第一次 flake，第二次 non-flake fail → attempts=2, reason="flake-retry-exhausted:<tag>"
    （第一次的 tag，因为 max 用完了）—— **不**改 tag 跟 stderr 错位的设计

集成 (`test_checkers_dev_cross_check.py` / `test_checkers_staging_test.py` /
`test_checkers_spec_lint.py`)：

- 已有的 pass / fail / timeout 路径不变（attempts=1 default 不破已有断言）
- 新增 1 条 case：mock RC 第一次返 flake stderr exit=1，第二次返 exit=0 → CheckResult
  passed=True, attempts=2, reason 含 "flake-retry-recovered"

非测试：本 REQ 不写 integration test (`tests/integration/`)，那是 challenger-agent 的活。
