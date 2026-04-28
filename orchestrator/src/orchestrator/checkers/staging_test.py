"""staging-test 自检（M1，for-each-repo 并行）：sisyphus 在 runner pod 对每个业务 repo
**并行**跑 `make ci-unit-test && make ci-integration-test`（单 repo 内串行），
收退出码决定 pass/fail。

不起 BKD agent，不靠 result:pass tag，sisyphus 是唯一裁判。

多仓重构后：遍历 /workspace/source/*，含 Makefile `ci-unit-test` + `ci-integration-test`
target 的仓并行起（per-repo 30min × N 串行会超 timeout）。

feat/<REQ> 缺失语义（REQ-checker-no-feat-branch-fail-loud-1777123726）：
sisyphus-clone-repos.sh 把 involved_repos 列的每个仓 clone 进 /workspace/source/，
所以 /workspace/source/<repo>/ 存在 ⇒ analyze-agent 声明该仓 involved，**必须**
推到 feat/<REQ> 分支。fetch + checkout feat/<REQ> 失败 → fail loud（fail=1 + 拒绝
silent-pass 信息），不再 [skip] 噤声。Makefile 缺 ci-unit-test/ci-integration-test
target 仍 [skip]（仓本身不可测，与 analyze-agent 无关）。

为何单 repo 内 unit/integration **串行**而非并行：
- ci-unit-test 内部已经 main + bmp 双并发跑 go test
- ci-integration-test 内部已经 main + bmp 双并发起 docker compose
- 两个 stage 再外层并发会叠加内存峰值,撑破 runner pod 8 GiB cgroup 限制
- 串行只多 ~2-5min,但单 pod 峰值减半,节点能并发跑更多 req

每仓输出落 /tmp/staging-test-logs/<repo>-<kind>.log（kind ∈ unit/int），
汇总阶段按仓 echo PASS/FAIL + tail 日志让 verifier 看清；任一失败整体红。

REQ-staging-test-baseline-diff-1777343371：两阶段 baseline diff。
Phase 1: checkout main HEAD，跑同套 ci-* 命令，收集 baseline_failures（24h PG 缓存）。
Phase 2: checkout feat/<REQ>，跑同套命令，收集 pr_failures。
真 fail set = pr_failures - baseline_failures：
  - 空集 → staging-test.pass（PR 没引入新失败）
  - 非空 → staging-test.fail，verifier 收到差量上下文
baseline run 失败 → 退化到老逻辑（直接判 PR exit_code）。
"""
from __future__ import annotations

import asyncio
import re

import structlog

from .. import k8s_runner
from ..config import settings
from ..store import baseline_results as _baseline_cache
from ..store import db as _db
from ._flake import run_with_flake_retry
from ._types import CheckResult

__all__ = ["CheckResult", "run_staging_test"]

log = structlog.get_logger(__name__)

_TAIL = 2048
_DEFAULT_TIMEOUT = 1800
_STAGE = "staging_test"

# 解析 === PASS/FAIL: <name> === 标记（PASS→stdout，FAIL→stderr）
_PASS_RE = re.compile(r"=== PASS: (\S+) ===")
_FAIL_RE = re.compile(r"=== FAIL: (\S+) ===")
_SHA_RE = re.compile(r"MAIN_SHA:\s+([0-9a-f]{40})")


def _build_cmd(req_id: str) -> str:
    """对含 ci-unit-test + ci-integration-test target 的每个 source repo 并行起；
    单 repo 内 unit → integration 串行（&&）。

    Empty-source guard（防 silent-pass）：
    - /workspace/source 不存在或没任何子目录 → 直接 exit 1
    - 任一 cloned repo 缺 feat/<REQ> 分支 → fail=1 + 拒绝 silent-pass stderr
      （REQ-checker-no-feat-branch-fail-loud-1777123726：clone helper 已克隆该仓 ⇒
      该仓 involved；analyze-agent 没推 feat 分支是结构性失败，不再 [skip] 噤声）
    - 遍历后 ran=0（所有仓都缺 ci-unit-test/ci-integration-test target）→ exit 1
      checker 不能在零信号情况下报 pass。

    每仓先切到 feat/<REQ>（agent 推到的分支）。
    pids 列表存 `pid:name`，结尾按 pid 依次 wait；失败 tail unit + int 各 50 行到 stderr。
    """
    return (
        "set -o pipefail; "
        "if [ ! -d /workspace/source ]; then "
        '  echo "=== FAIL staging_test: /workspace/source missing — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "repo_count=$(find /workspace/source -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l); "
        'if [ "$repo_count" -eq 0 ]; then '
        '  echo "=== FAIL staging_test: /workspace/source empty (0 cloned repos) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "fail=0; "
        "ran=0; "
        "mkdir -p /tmp/staging-test-logs; "
        'pids=""; '
        "for repo in /workspace/source/*/; do "
        '  name=$(basename "$repo"); '
        # 暴露 fetch 真错（之前 2>/dev/null 把 auth/network/dns 失败全吞，silent-pass
        # guard 一律打成"branch 不存在"，REQ-ttpos-pat-validate-v4 实证：branch 真在
        # origin，fetch 失败原因被掩盖 → verifier 8min 思考还判不准）。
        # 现在：捕 stderr，rev-parse 单独验 origin ref 是否真到位，失败时把 fetch 真
        # 错塞进 silent-pass 信息里。
        f'  fetch_err=$(cd "$repo" && git fetch origin "feat/{req_id}" 2>&1 || true); '
        f'  if ! (cd "$repo" && git rev-parse --verify "refs/remotes/origin/feat/{req_id}" >/dev/null 2>&1); then '
        f'    echo "=== FAIL staging_test: $name has no feat/{req_id} branch reachable on origin — refusing to silent-pass ===" >&2; '
        '    echo "git fetch stderr: $fetch_err" >&2; '
        "    fail=1; "
        "    continue; "
        "  fi; "
        f'  cd "$repo" && git checkout -B "feat/{req_id}" "origin/feat/{req_id}" >/dev/null 2>&1; '
        # ci-unit-test / ci-integration-test target 检测：解析 Makefile（含 include 子 mk）
        # 而非 grep 顶层（实证 ttpos-server-go：ci-* 在 ttpos-scripts/lint-ci-test.mk via include
        # → 顶层 grep 漏判 → "0 source repos eligible" silent fail）。
        # `make -p -n || true` 抑制 Makefile 评估期错误（pipefail 否则吞 grep 输出）。
        # 实证：ttpos v8 dev_cross_check 同款卡，根因 make exit 非零。
        '  if [ -f "$repo/Makefile" ] && (cd "$repo" && (make -p -n 2>/dev/null || true) | grep -qE \'^ci-unit-test:\') '
        '       && (cd "$repo" && (make -p -n 2>/dev/null || true) | grep -qE \'^ci-integration-test:\'); then '
        # 先 ci-setup 拉 deps，跟 dev_cross_check / ttpos-ci ci-go.yml 流对齐。
        '    echo "=== staging_test (ci-setup): $name ==="; '
        '    (cd "$repo" && make ci-setup) || true; '
        "    ( "
        '      echo "=== staging_test (unit): $name ==="; '
        '      cd "$repo" && make ci-unit-test > "/tmp/staging-test-logs/$name-unit.log" 2>&1 '
        '      && echo "=== staging_test (integration): $name ===" '
        '      && make ci-integration-test > "/tmp/staging-test-logs/$name-int.log" 2>&1 '
        "    ) & "
        '    pids="$pids $!:$name"; '
        "    ran=$((ran+1)); "
        "  else "
        '    echo "[skip] $name: missing ci-unit-test or ci-integration-test target"; '
        "  fi; "
        "done; "
        'if [ "$ran" -eq 0 ] && [ "$fail" -eq 0 ]; then '
        f'  echo "=== FAIL staging_test: 0 source repos eligible (no ci-unit-test+ci-integration-test target on feat/{req_id}) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "for pid_name in $pids; do "
        "  pid=${pid_name%%:*}; "
        "  name=${pid_name##*:}; "
        "  if ! wait $pid; then "
        '    echo "=== FAIL: $name ===" >&2; '
        '    echo "--- $name unit log (tail 50) ---" >&2; '
        '    tail -50 "/tmp/staging-test-logs/$name-unit.log" >&2 2>/dev/null || true; '
        '    echo "--- $name integration log (tail 50) ---" >&2; '
        '    tail -50 "/tmp/staging-test-logs/$name-int.log" >&2 2>/dev/null || true; '
        "    fail=1; "
        "  else "
        '    echo "=== PASS: $name ==="; '
        '    tail -10 "/tmp/staging-test-logs/$name-int.log" 2>/dev/null || true; '
        "  fi; "
        "done; "
        "[ $fail -eq 0 ]"
    )


def _build_get_main_sha_cmd() -> str:
    """从第一个可访问的 source repo 快速取 origin/main HEAD SHA。
    成功输出 "MAIN_SHA: <40-char-sha>"，失败 exit 1。
    """
    return (
        "sha=''; "
        "for repo in /workspace/source/*/; do "
        "  if [ -d \"$repo\" ]; then "
        "    cd \"$repo\" && git fetch origin main 2>/dev/null; "
        "    sha=$(git rev-parse origin/main 2>/dev/null || true); "
        "    if [ -n \"$sha\" ]; then break; fi; "
        "  fi; "
        "done; "
        "[ -n \"$sha\" ] && echo \"MAIN_SHA: $sha\" || exit 1"
    )


def _build_baseline_cmd() -> str:
    """Checkout origin/main 对每个 source repo 并行跑 ci-* 测试，收集 baseline_failures。

    结构与 _build_cmd 一致（相同 PASS/FAIL 标记，相同 ci-setup/unit/integration 流），
    但不要求 feat/<REQ> 分支（baseline 跑 main，不是 agent 的 PR 分支）。
    最后输出 "MAIN_SHA: <sha>" 给调用方缓存用。

    失败仓 → 整体 exit 1，但已发出的 PASS/FAIL 标记仍可解析（partial 也有用）。
    baseline run 异常（exit 1 + 0 标记解析到）→ 调用方退化到老逻辑。
    """
    return (
        "set -o pipefail; "
        "if [ ! -d /workspace/source ]; then "
        '  echo "=== FAIL baseline: /workspace/source missing ===" >&2; '
        "  exit 1; "
        "fi; "
        "repo_count=$(find /workspace/source -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l); "
        'if [ "$repo_count" -eq 0 ]; then '
        '  echo "=== FAIL baseline: /workspace/source empty ===" >&2; '
        "  exit 1; "
        "fi; "
        "fail=0; ran=0; main_sha=''; "
        "mkdir -p /tmp/baseline-logs; "
        'pids=""; '
        "for repo in /workspace/source/*/; do "
        '  name=$(basename "$repo"); '
        '  if ! (cd "$repo" && git fetch origin main 2>/dev/null && '
        "         git checkout -B _sisyphus_baseline origin/main >/dev/null 2>&1); then "
        '    echo "[baseline-skip] $name: failed to checkout origin/main" >&2; '
        "    continue; "
        "  fi; "
        '  if [ -z "$main_sha" ]; then '
        '    main_sha=$(cd "$repo" && git rev-parse HEAD 2>/dev/null || true); '
        "  fi; "
        '  if [ -f "$repo/Makefile" ] '
        '       && (cd "$repo" && (make -p -n 2>/dev/null || true) | grep -qE \'^ci-unit-test:\') '
        '       && (cd "$repo" && (make -p -n 2>/dev/null || true) | grep -qE \'^ci-integration-test:\'); then '
        '    echo "=== baseline (ci-setup): $name ==="; '
        '    (cd "$repo" && make ci-setup) || true; '
        "    ( "
        '      cd "$repo" '
        '      && make ci-unit-test > "/tmp/baseline-logs/$name-unit.log" 2>&1 '
        '      && make ci-integration-test > "/tmp/baseline-logs/$name-int.log" 2>&1 '
        "    ) & "
        '    pids="$pids $!:$name"; '
        "    ran=$((ran+1)); "
        "  else "
        '    echo "[baseline-skip] $name: missing ci targets"; '
        "  fi; "
        "done; "
        'if [ "$ran" -eq 0 ]; then '
        '  echo "=== FAIL baseline: 0 repos eligible ===" >&2; '
        "  exit 1; "
        "fi; "
        "for pid_name in $pids; do "
        "  pid=${pid_name%%:*}; "
        "  name=${pid_name##*:}; "
        "  if ! wait $pid; then "
        '    echo "=== FAIL: $name ===" >&2; '
        "    fail=1; "
        "  else "
        '    echo "=== PASS: $name ==="; '
        "  fi; "
        "done; "
        '[ -n "$main_sha" ] && echo "MAIN_SHA: $main_sha"; '
        "[ $fail -eq 0 ]"
    )


def _parse_repo_results(stdout: str, stderr: str) -> dict[str, bool]:
    """解析 === PASS/FAIL: <name> === 标记。返回 {repo_basename: passed}。

    PASS 标记在 stdout，FAIL 标记在 stderr（与 _build_cmd 输出方向一致）。
    解析失败返空 dict；调用方按空 dict 退化处理。
    """
    results: dict[str, bool] = {}
    for m in _PASS_RE.finditer(stdout):
        results[m.group(1)] = True
    for m in _FAIL_RE.finditer(stderr):
        results[m.group(1)] = False
    return results


def _parse_main_sha(stdout: str) -> str | None:
    """从命令输出提取 MAIN_SHA: <sha>。"""
    m = _SHA_RE.search(stdout)
    return m.group(1) if m else None


def _compute_diff(
    baseline: dict[str, bool],
    pr_repos: dict[str, bool],
) -> tuple[set[str], set[str], set[str]]:
    """返回 (baseline_failures, pr_failures, pr_introduced)。

    pr_introduced = pr_failures - baseline_failures（PR 新引入的失败）。
    """
    baseline_failures = {r for r, p in baseline.items() if not p}
    pr_failures = {r for r, p in pr_repos.items() if not p}
    return baseline_failures, pr_failures, pr_failures - baseline_failures


def _format_diff_header(
    main_sha: str | None,
    baseline_failures: set[str],
    pr_failures: set[str],
    introduced: set[str],
) -> str:
    verdict = "PASS (no new failures)" if not introduced else "FAIL (new failures introduced)"
    return (
        f"=== SISYPHUS BASELINE DIFF: {verdict} ===\n"
        f"main_sha: {main_sha or 'unknown'}\n"
        f"baseline_failures: {sorted(baseline_failures) or '[]'}\n"
        f"pr_introduced_failures: {sorted(introduced) or '[]'}\n"
        f"all_pr_failures: {sorted(pr_failures) or '[]'}\n"
        "=========================================\n\n"
    )


async def run_staging_test(req_id: str) -> CheckResult:
    """两阶段 baseline diff staging test。

    Phase 1: baseline（main HEAD，24h PG 缓存）
    Phase 2: PR 测试（feat/<REQ>，infra-flake retry）
    Phase 3: 差量判定

    baseline run 失败/异常 → 退化到老逻辑（直接判 PR exit_code），不拖死 stage。

    REQ-checker-infra-flake-retry-1777247423：PR 阶段用 run_with_flake_retry 包 exec，
    DNS / kubectl-channel / registry-rate-limit / go-mod 等 infra 抖动自动重跑。
    baseline 阶段不做 flake retry（失败直接退化，不阻塞）。
    """
    rc = k8s_runner.get_controller()
    obs_pool = _db.get_obs_pool()  # 可能为 None（obs dsn 未配置时）

    log.info("checker.staging_test.start", req_id=req_id, timeout=_DEFAULT_TIMEOUT)

    # ── Phase 1: Baseline（带 24h 缓存）────────────────────────────────────
    baseline_repos: dict[str, bool] | None = None
    main_sha: str | None = None

    try:
        sha_result = await asyncio.wait_for(
            rc.exec_in_runner(req_id, _build_get_main_sha_cmd(), timeout_sec=60),
            timeout=70,
        )
        main_sha = _parse_main_sha(sha_result.stdout)

        if main_sha:
            cache_key = f"baseline:staging_test:{main_sha}"

            if obs_pool is not None:
                baseline_repos = await _baseline_cache.get_cached(obs_pool, cache_key)
                if baseline_repos is not None:
                    log.info(
                        "checker.staging_test.baseline_cache_hit",
                        req_id=req_id, main_sha=main_sha[:8], repos=baseline_repos,
                    )

            if baseline_repos is None:
                log.info(
                    "checker.staging_test.baseline_run_start",
                    req_id=req_id, main_sha=main_sha[:8],
                )
                bl_raw = await asyncio.wait_for(
                    rc.exec_in_runner(req_id, _build_baseline_cmd(), timeout_sec=_DEFAULT_TIMEOUT),
                    timeout=_DEFAULT_TIMEOUT + 10,
                )
                baseline_repos = _parse_repo_results(bl_raw.stdout, bl_raw.stderr)
                # 从 baseline 命令输出中也可以拿到 sha（更精确，是 HEAD 而非 origin/main）
                sha_from_bl = _parse_main_sha(bl_raw.stdout)
                if sha_from_bl:
                    main_sha = sha_from_bl
                    cache_key = f"baseline:staging_test:{main_sha}"
                log.info(
                    "checker.staging_test.baseline_run_done",
                    req_id=req_id, results=baseline_repos, main_sha=main_sha[:8],
                )
                # 只在有解析到结果时才缓存（避免缓存 infra crash 的空结果）
                if baseline_repos and obs_pool is not None:
                    await _baseline_cache.put_cached(obs_pool, cache_key, main_sha, baseline_repos)

    except Exception as exc:
        log.warning(
            "checker.staging_test.baseline_phase_failed",
            req_id=req_id, error=str(exc)[:200],
        )
        # 退化到老逻辑：baseline_repos 保持 None

    # ── Phase 2: PR 测试（含 infra-flake retry）────────────────────────────
    cmd = _build_cmd(req_id)

    max_retries = (
        settings.checker_infra_flake_retry_max
        if settings.checker_infra_flake_retry_enabled
        else 0
    )

    async def _run_once():
        return await asyncio.wait_for(
            rc.exec_in_runner(req_id, cmd, timeout_sec=_DEFAULT_TIMEOUT),
            timeout=_DEFAULT_TIMEOUT + 10,
        )

    result, attempts, flake_reason = await run_with_flake_retry(
        coro_factory=_run_once,
        stage=_STAGE,
        req_id=req_id,
        max_retries=max_retries,
        backoff_sec=settings.checker_infra_flake_retry_backoff_sec,
    )

    # ── Phase 3: Diff ────────────────────────────────────────────────────
    pr_repos = _parse_repo_results(result.stdout, result.stderr)

    # baseline_repos 非空（至少解析到 1 个 repo 结果）且 PR 有失败时才做差量判定。
    # baseline_repos 空 dict 视为"baseline 无可用数据"，退化到老逻辑。
    if baseline_repos and result.exit_code != 0:
        baseline_failures, pr_failures, introduced = _compute_diff(baseline_repos, pr_repos)
        diff_header = _format_diff_header(main_sha, baseline_failures, pr_failures, introduced)

        if not introduced:
            # PR 没引入新失败：main 自己就坏了，override 到 pass
            log.info(
                "checker.staging_test.baseline_diff_override_pass",
                req_id=req_id, baseline_failures=sorted(baseline_failures),
                pr_failures=sorted(pr_failures),
            )
            return CheckResult(
                passed=True,
                exit_code=0,
                stdout_tail=(diff_header + result.stdout)[-_TAIL:],
                stderr_tail=result.stderr[-_TAIL:],
                duration_sec=result.duration_sec,
                cmd=cmd,
                reason=flake_reason or "baseline-diff-pass",
                attempts=attempts,
            )

        # 有新失败：fail，stderr 前置 diff 上下文供 verifier 判断
        log.info(
            "checker.staging_test.baseline_diff_new_failures",
            req_id=req_id, baseline_failures=sorted(baseline_failures),
            introduced=sorted(introduced),
        )
        return CheckResult(
            passed=False,
            exit_code=result.exit_code,
            stdout_tail=result.stdout[-_TAIL:],
            stderr_tail=(diff_header + result.stderr)[-_TAIL:],
            duration_sec=result.duration_sec,
            cmd=cmd,
            reason=flake_reason,
            attempts=attempts,
        )

    # ── 老逻辑 fallback（baseline 不可用 or PR 已经 pass）───────────────
    passed = result.exit_code == 0
    log.info(
        "checker.staging_test.done",
        req_id=req_id, passed=passed, exit_code=result.exit_code,
        duration_sec=round(result.duration_sec, 1),
        attempts=attempts, flake_reason=flake_reason,
    )

    return CheckResult(
        passed=passed,
        exit_code=result.exit_code,
        stdout_tail=result.stdout[-_TAIL:],
        stderr_tail=result.stderr[-_TAIL:],
        duration_sec=result.duration_sec,
        cmd=cmd,
        reason=flake_reason,
        attempts=attempts,
    )
