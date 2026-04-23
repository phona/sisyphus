"""dev_cross_check：开发交叉验证（M1 checker，for-each-repo）。

多仓重构后：每个 source repo 在 runner pod 里挂在 /workspace/source/<repo-name>/。
checker 遍历 /workspace/source/*，含 Makefile 的仓逐一跑 `make dev-cross-check`；
任一失败整体红。每仓失败时 echo `=== FAIL: $repo ===` 到 stderr。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from .. import k8s_runner
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048


def _build_cmd(req_id: str) -> str:
    """遍历 /workspace/source/*/，先切到 feat/<REQ>，对含 Makefile 的仓跑 make dev-cross-check。

    fetch + checkout feat/<REQ> 失败 → 该仓 not involved，跳过不算 fail。
    """
    return (
        "set -o pipefail; "
        "fail=0; "
        "for repo in /workspace/source/*/; do "
        '  name=$(basename "$repo"); '
        f'  if ! (cd "$repo" && git fetch origin "feat/{req_id}" 2>/dev/null && git checkout -B "feat/{req_id}" "origin/feat/{req_id}" 2>/dev/null); then '
        '    echo "[skip] $name: no feat branch / not involved"; '
        "    continue; "
        "  fi; "
        '  if [ -f "$repo/Makefile" ]; then '
        '    echo "=== dev_cross_check: $name ==="; '
        '    if ! (cd "$repo" && make dev-cross-check); then '
        '      echo "=== FAIL: $name ===" >&2; '
        "      fail=1; "
        "    fi; "
        "  fi; "
        "done; "
        "exit $fail"
    )


async def run_dev_cross_check(
    req_id: str,
    *,
    timeout_sec: int = 300,
) -> CheckResult:
    """kubectl exec runner -- <for-each-repo make dev-cross-check>。"""
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id)
    log.info(
        "checker.dev_cross_check.start",
        req_id=req_id, timeout=timeout_sec,
    )
    started = time.monotonic()

    try:
        exec_result = await asyncio.wait_for(
            rc.exec_in_runner(req_id, cmd, timeout_sec=timeout_sec),
            timeout=timeout_sec + 10,
        )
    except TimeoutError:
        log.error(
            "checker.dev_cross_check.timeout", req_id=req_id,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"dev cross-check 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.dev_cross_check.done",
        req_id=req_id,
        passed=passed, exit_code=exec_result.exit_code,
        duration_sec=round(exec_result.duration_sec, 2),
    )
    return CheckResult(
        passed=passed,
        exit_code=exec_result.exit_code,
        stdout_tail=exec_result.stdout[-_TAIL:],
        stderr_tail=exec_result.stderr[-_TAIL:],
        duration_sec=exec_result.duration_sec,
        cmd=cmd,
    )
