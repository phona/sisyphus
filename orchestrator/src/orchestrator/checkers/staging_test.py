"""staging-test 自检（M1，for-each-repo 并行）：sisyphus 在 runner pod 对每个业务 repo
并行跑 `make ci-test`，收退出码决定 pass/fail。

不起 BKD agent，不靠 result:pass tag，sisyphus 是唯一裁判。

多仓重构后：遍历 /workspace/source/*，含 Makefile ci-test target 的仓并行跑
（per-repo 30min × N 串行会超 timeout）。每仓输出落 /tmp/staging-test-logs/<repo>.log，
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
    """对含 ci-test target 的每个 source repo 并行跑 make ci-test。

    每仓先切到 feat/<REQ>（agent 推到的分支）；fetch/checkout 失败 → not involved 跳过。
    pids 列表存 `pid:name`，结尾按 pid 依次 wait；失败 tail 最后 50 行到 stderr。
    """
    return (
        "set -o pipefail; "
        "fail=0; "
        "mkdir -p /tmp/staging-test-logs; "
        'pids=""; '
        "for repo in /workspace/source/*/; do "
        '  name=$(basename "$repo"); '
        f'  if ! (cd "$repo" && git fetch origin "feat/{req_id}" 2>/dev/null && git checkout -B "feat/{req_id}" "origin/feat/{req_id}" 2>/dev/null); then '
        '    echo "[skip] $name: no feat branch / not involved"; '
        "    continue; "
        "  fi; "
        '  if [ -f "$repo/Makefile" ] && grep -q \'^ci-test:\' "$repo/Makefile"; then '
        "    ( "
        '      echo "=== staging_test: $name ==="; '
        '      cd "$repo" && make ci-test '
        '    ) > "/tmp/staging-test-logs/$name.log" 2>&1 & '
        '    pids="$pids $!:$name"; '
        "  fi; "
        "done; "
        "for pid_name in $pids; do "
        "  pid=${pid_name%%:*}; "
        "  name=${pid_name##*:}; "
        "  if ! wait $pid; then "
        '    echo "=== FAIL: $name ===" >&2; '
        '    tail -50 "/tmp/staging-test-logs/$name.log" >&2; '
        "    fail=1; "
        "  else "
        '    echo "=== PASS: $name ==="; '
        '    tail -10 "/tmp/staging-test-logs/$name.log"; '
        "  fi; "
        "done; "
        "[ $fail -eq 0 ]"
    )


async def run_staging_test(req_id: str) -> CheckResult:
    """在 runner pod 并行对每个 source repo 跑 make ci-test，收退出码决定 pass/fail。"""
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
