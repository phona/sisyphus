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
from ..config import settings
from ._flake import run_with_flake_retry
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048
_STAGE = "spec_lint"


def _build_cmd(req_id: str) -> str:
    """遍历 /workspace/source/*/，先切到 feat/<REQ>，对含 openspec/changes/<REQ>/ 的仓跑两项检查。

    Empty-source guard（防 silent-pass）：
    - /workspace/source 不存在或没任何子目录 → 直接 exit 1（clone helper 没跑 / PVC 被擦）
    - 遍历后 ran=0（每仓都被 skip：feat 分支 fetch 不到 / 没 openspec/changes/<REQ>/）
      → exit 1。checker 不能在零信号情况下报 pass。

    主循环：
    1. git fetch origin feat/<REQ> + git checkout -B feat/<REQ> origin/feat/<REQ>
       —— spec/dev 文件由 agent 推到 feat/<REQ> 分支，runner pod 默认在 main。
       fetch 失败 / 没有该 branch → 该仓视为 not involved（agent 没改它），跳过不算 fail。
    2. openspec validate openspec/changes/<REQ>
    3. check-scenario-refs.sh --specs-search-path /workspace/source .

    任一仓任一检查失败 → exit 1。
    """
    return (
        "set -o pipefail; "
        "if [ ! -d /workspace/source ]; then "
        '  echo "=== FAIL spec_lint: /workspace/source missing — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "repo_count=$(find /workspace/source -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l); "
        'if [ "$repo_count" -eq 0 ]; then '
        '  echo "=== FAIL spec_lint: /workspace/source empty (0 cloned repos) — refusing to silent-pass ===" >&2; '
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
        f'  if [ -d "$repo/openspec/changes/{req_id}" ]; then '
        '    echo "=== spec_lint: $name ==="; '
        f'    if ! (cd "$repo" && openspec validate "{req_id}"); then '
        '      echo "=== FAIL: $name ===" >&2; '
        "      fail=1; "
        "    fi; "
        '    if ! (cd "$repo" && check-scenario-refs.sh --specs-search-path /workspace/source .); then '
        '      echo "=== FAIL scenario-refs: $name ===" >&2; '
        "      fail=1; "
        "    fi; "
        "    ran=$((ran+1)); "
        "  else "
        f'    echo "[skip] $name: no openspec/changes/{req_id}/"; '
        "  fi; "
        "done; "
        'if [ "$ran" -eq 0 ]; then '
        f'  echo "=== FAIL spec_lint: 0 source repos eligible (no feat/{req_id} branch with openspec/changes/{req_id}/) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "[ $fail -eq 0 ]"
    )


async def run_spec_lint(
    req_id: str,
    *,
    timeout_sec: int = 120,
) -> CheckResult:
    """kubectl exec runner -- <for-each-repo openspec validate + scenario refs>。

    REQ-checker-infra-flake-retry-1777247423：用 run_with_flake_retry 包 exec，
    DNS / kubectl-channel / github-fetch 等 infra 抖动自动重跑，bounded by settings。
    """
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id)
    log.info(
        "checker.spec_lint.start",
        req_id=req_id, timeout=timeout_sec,
    )
    started = time.monotonic()

    max_retries = (
        settings.checker_infra_flake_retry_max
        if settings.checker_infra_flake_retry_enabled
        else 0
    )

    async def _run_once():
        return await asyncio.wait_for(
            rc.exec_in_runner(req_id, cmd, timeout_sec=timeout_sec),
            timeout=timeout_sec + 10,
        )

    try:
        exec_result, attempts, flake_reason = await run_with_flake_retry(
            coro_factory=_run_once,
            stage=_STAGE,
            req_id=req_id,
            max_retries=max_retries,
            backoff_sec=settings.checker_infra_flake_retry_backoff_sec,
        )
    except TimeoutError:
        log.error(
            "checker.spec_lint.timeout", req_id=req_id,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"spec lint 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
            reason="timeout",
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.spec_lint.done",
        req_id=req_id,
        passed=passed, exit_code=exec_result.exit_code,
        duration_sec=round(exec_result.duration_sec, 2),
        attempts=attempts, flake_reason=flake_reason,
    )
    return CheckResult(
        passed=passed,
        exit_code=exec_result.exit_code,
        stdout_tail=exec_result.stdout[-_TAIL:],
        stderr_tail=exec_result.stderr[-_TAIL:],
        duration_sec=exec_result.duration_sec,
        cmd=cmd,
        reason=flake_reason,
        attempts=attempts,
    )
