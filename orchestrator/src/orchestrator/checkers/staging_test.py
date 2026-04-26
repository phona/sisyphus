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
"""
from __future__ import annotations

import asyncio

import structlog

from .. import k8s_runner
from ._types import CheckResult

__all__ = ["CheckResult", "run_staging_test"]

log = structlog.get_logger(__name__)

_TAIL = 2048
_DEFAULT_TIMEOUT = 1800


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
        '  if [ -f "$repo/Makefile" ] && (cd "$repo" && make -p -n 2>/dev/null | grep -qE \'^ci-unit-test:\') '
        '       && (cd "$repo" && make -p -n 2>/dev/null | grep -qE \'^ci-integration-test:\'); then '
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


async def run_staging_test(req_id: str) -> CheckResult:
    """在 runner pod 并行对每个 source repo 跑 ci-unit-test && ci-integration-test，收退出码决定 pass/fail。"""
    cmd = _build_cmd(req_id)
    timeout_sec = _DEFAULT_TIMEOUT

    rc = k8s_runner.get_controller()
    log.info(
        "checker.staging_test.start",
        req_id=req_id, timeout=timeout_sec,
    )

    result = await asyncio.wait_for(
        rc.exec_in_runner(req_id, cmd, timeout_sec=timeout_sec),
        timeout=timeout_sec + 10,
    )

    passed = result.exit_code == 0
    log.info(
        "checker.staging_test.done",
        req_id=req_id, passed=passed, exit_code=result.exit_code,
        duration_sec=round(result.duration_sec, 1),
    )

    return CheckResult(
        passed=passed,
        exit_code=result.exit_code,
        stdout_tail=result.stdout[-_TAIL:],
        stderr_tail=result.stderr[-_TAIL:],
        duration_sec=result.duration_sec,
        cmd=cmd,
    )
