"""spec 完整性检查（M1）：openspec validate + scenario refs linter（for-each-repo）。

多仓重构后：每个 source repo 在 runner pod 里挂在 /workspace/source/<repo-name>/，
各自带 openspec/changes/<REQ>/。checker 遍历 /workspace/source/*，含本 REQ 目录
的仓逐一跑 openspec validate + check-scenario-refs.sh；任一失败整体红。

每仓失败时 echo `=== FAIL: $repo ===` 到 stderr 让 verifier 看清。
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
    """遍历 /workspace/source/*/，对含 openspec/changes/<REQ>/ 的仓跑两项检查。

    1. openspec validate openspec/changes/<REQ>
    2. check-scenario-refs.sh --specs-search-path /workspace/source .

    任一仓任一检查失败 → exit 1。check-scenario-refs.sh 的
    --specs-search-path flag 支持跨仓 scenario 引用（Agent B 实现）。
    """
    return (
        "set -o pipefail; "
        "fail=0; "
        "for repo in /workspace/source/*/; do "
        f'  if [ -d "$repo/openspec/changes/{req_id}" ]; then '
        '    name=$(basename "$repo"); '
        '    echo "=== spec_lint: $name ==="; '
        f'    if ! (cd "$repo" && openspec validate "openspec/changes/{req_id}"); then '
        '      echo "=== FAIL: $name ===" >&2; '
        "      fail=1; "
        "    fi; "
        '    if ! (cd "$repo" && check-scenario-refs.sh --specs-search-path /workspace/source .); then '
        '      echo "=== FAIL scenario-refs: $name ===" >&2; '
        "      fail=1; "
        "    fi; "
        "  fi; "
        "done; "
        "exit $fail"
    )


async def run_spec_lint(
    req_id: str,
    *,
    timeout_sec: int = 120,
) -> CheckResult:
    """kubectl exec runner -- <for-each-repo openspec validate + scenario refs>。"""
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id)
    log.info(
        "checker.spec_lint.start",
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
            "checker.spec_lint.timeout", req_id=req_id,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"spec lint 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.spec_lint.done",
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
