"""dev_cross_check：开发交叉验证（M1 checker，for-each-repo）。

多仓重构后：每个 source repo 在 runner pod 里挂在 /workspace/source/<repo-name>/。
checker 遍历 /workspace/source/*，含 Makefile `ci-lint` target 的仓逐一跑
`BASE_REV=$(git merge-base HEAD origin/main) make ci-lint`；任一失败整体红。
每仓失败时 echo `=== FAIL: $repo ===` 到 stderr。

ci-lint 是 ttpos-ci 标准契约：仅 lint 变更文件 (BASE_REV 缺失则全量)。
fetch + checkout feat/<REQ> 失败 → 该仓 not involved，跳过不算 fail。
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
    """遍历 /workspace/source/*/，先切到 feat/<REQ>，对含 ci-lint target 的仓跑 make ci-lint。

    Empty-source guard（防 silent-pass）：
    - /workspace/source 不存在或没任何子目录 → 直接 exit 1
    - 遍历后 ran=0（feat 分支 fetch 不到 / 没 ci-lint target）→ exit 1
      checker 不能在零信号情况下报 pass。

    BASE_REV 计算：`git merge-base HEAD origin/main`，fallback `origin/develop`、
    `origin/dev`，再失败传空字符串（ci-lint 退化为全量扫描）。
    """
    return (
        "set -o pipefail; "
        "if [ ! -d /workspace/source ]; then "
        '  echo "=== FAIL dev_cross_check: /workspace/source missing — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "repo_count=$(find /workspace/source -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l); "
        'if [ "$repo_count" -eq 0 ]; then '
        '  echo "=== FAIL dev_cross_check: /workspace/source empty (0 cloned repos) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "fail=0; "
        "ran=0; "
        "for repo in /workspace/source/*/; do "
        '  name=$(basename "$repo"); '
        f'  if ! (cd "$repo" && git fetch origin "feat/{req_id}" 2>/dev/null && git checkout -B "feat/{req_id}" "origin/feat/{req_id}" 2>/dev/null); then '
        '    echo "[skip] $name: no feat branch / not involved"; '
        "    continue; "
        "  fi; "
        '  if [ -f "$repo/Makefile" ] && grep -q \'^ci-lint:\' "$repo/Makefile"; then '
        '    base_rev=$(cd "$repo" && (git merge-base HEAD origin/main 2>/dev/null '
        '              || git merge-base HEAD origin/develop 2>/dev/null '
        '              || git merge-base HEAD origin/dev 2>/dev/null '
        '              || echo "")); '
        '    echo "=== dev_cross_check (ci-lint): $name (BASE_REV=$base_rev) ==="; '
        '    if ! (cd "$repo" && BASE_REV="$base_rev" make ci-lint); then '
        '      echo "=== FAIL: $name ===" >&2; '
        "      fail=1; "
        "    fi; "
        "    ran=$((ran+1)); "
        "  else "
        '    echo "[skip] $name: no make ci-lint target"; '
        "  fi; "
        "done; "
        'if [ "$ran" -eq 0 ]; then '
        f'  echo "=== FAIL dev_cross_check: 0 source repos eligible (no feat/{req_id} branch with make ci-lint target) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "[ $fail -eq 0 ]"
    )


async def run_dev_cross_check(
    req_id: str,
    *,
    timeout_sec: int = 300,
) -> CheckResult:
    """kubectl exec runner -- <for-each-repo make ci-lint>。"""
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
