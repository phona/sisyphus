"""analyze 阶段 post-artifact-check（REQ-analyze-artifact-check-1777254586）。

夹在 ANALYZING → SPEC_LINT_RUNNING 之间，机械校 analyze BKD agent 的产物：
prevent agent self-reporting pass with no artifacts.

校验规则（分两层）：
- 仓级（每个 eligible 仓必须满足）：
    * `openspec/changes/<REQ>/specs/*/spec.md` 至少一个非空文件
- 累积（所有 eligible 仓加起来满足）：
    * 至少一个非空 `openspec/changes/<REQ>/proposal.md`
    * 至少一个非空 `openspec/changes/<REQ>/tasks.md`，并且其内容包含至少一个
      Markdown checkbox（`- [ ]` / `- [x]` / `- [X]`）
  这种"累积"语义是为了不误伤跨仓 spec-home REQ —— consumer 仓常只放 spec.md，
  proposal / tasks 只在 spec home 仓里有一份。

eligible = 该仓有远程 `feat/<REQ>` 分支、能本地 fetch + checkout，且仓里有
`openspec/changes/<REQ>/` 目录。仓没改过本 REQ 则 fetch 失败 / 没 changes 目录
→ 直接 skip（不算 fail），与 spec_lint 同语义。

empty-source guards 与 spec_lint 对齐（防 silent-pass）：
- /workspace/source 缺失 / 0 子目录 → 直接 exit 1
- 0 仓 eligible（agent 一份产物都没 push）→ exit 1
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
_STAGE = "analyze_artifact_check"


def _build_cmd(req_id: str) -> str:
    """生成 runner pod 内执行的 POSIX shell。

    主循环每仓：
    1. `git fetch origin feat/<REQ>` + `git checkout -B feat/<REQ> origin/feat/<REQ>`
       —— 失败视为该仓 not involved（agent 没改它），跳过不算 fail
    2. eligible 仓必须有 `openspec/changes/<REQ>/specs/<capability>/spec.md`
       至少一个非空文件
    3. eligible 仓如果有非空 proposal.md / 含 checkbox 的 tasks.md，记进
       cumulative flag（has_proposal / has_tasks）
    遍历完后：
    - 任一 eligible 仓缺 spec.md → fail
    - 累积 has_proposal / has_tasks 任一为 0 → fail
    - 0 仓 eligible → exit 1（与 spec_lint 一致）
    """
    return (
        "set -o pipefail; "
        "if [ ! -d /workspace/source ]; then "
        '  echo "=== FAIL analyze-artifact-check: /workspace/source missing — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "repo_count=$(find /workspace/source -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l); "
        'if [ "$repo_count" -eq 0 ]; then '
        '  echo "=== FAIL analyze-artifact-check: /workspace/source empty (0 cloned repos) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "fail=0; "
        "ran=0; "
        "has_proposal=0; "
        "has_tasks=0; "
        "for repo in /workspace/source/*/; do "
        '  name=$(basename "$repo"); '
        f'  if ! (cd "$repo" && git fetch origin "feat/{req_id}" 2>/dev/null && git checkout -B "feat/{req_id}" "origin/feat/{req_id}" 2>/dev/null); then '
        '    echo "[skip] $name: no feat branch / not involved"; '
        "    continue; "
        "  fi; "
        f'  ch="$repo/openspec/changes/{req_id}"; '
        '  if [ ! -d "$ch" ]; then '
        f'    echo "[skip] $name: no openspec/changes/{req_id}/"; '
        "    continue; "
        "  fi; "
        "  ran=$((ran+1)); "
        '  echo "=== analyze-artifact-check: $name ==="; '
        # spec.md per-repo（必须）
        '  spec_count=$(find "$ch/specs" -name spec.md -type f -size +0 2>/dev/null | wc -l); '
        '  if [ "$spec_count" -eq 0 ]; then '
        f'    echo "=== FAIL: $name: openspec/changes/{req_id}/specs/<capability>/spec.md missing or all empty ===" >&2; '
        "    fail=1; "
        "  fi; "
        # proposal.md 累积
        '  if [ -s "$ch/proposal.md" ]; then has_proposal=1; fi; '
        # tasks.md 累积 + checkbox 校验
        '  if [ -s "$ch/tasks.md" ]; then '
        '    if grep -E "^[[:space:]]*-[[:space:]]*\\[[ xX]\\]" "$ch/tasks.md" >/dev/null 2>&1; then '
        "      has_tasks=1; "
        "    fi; "
        "  fi; "
        "done; "
        'if [ "$ran" -eq 0 ]; then '
        f'  echo "=== FAIL analyze-artifact-check: 0 source repos eligible (no feat/{req_id} branch with openspec/changes/{req_id}/) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        'if [ "$has_proposal" -eq 0 ]; then '
        f'  echo "=== FAIL analyze-artifact-check: no eligible repo has a non-empty openspec/changes/{req_id}/proposal.md ===" >&2; '
        "  fail=1; "
        "fi; "
        'if [ "$has_tasks" -eq 0 ]; then '
        f'  echo "=== FAIL analyze-artifact-check: no eligible repo has openspec/changes/{req_id}/tasks.md with at least one Markdown checkbox ===" >&2; '
        "  fail=1; "
        "fi; "
        "[ $fail -eq 0 ]"
    )


async def run_analyze_artifact_check(
    req_id: str,
    *,
    timeout_sec: int = 120,
) -> CheckResult:
    """kubectl exec runner -- <for-each-repo proposal/tasks/spec.md 校验>。

    复用 _flake.run_with_flake_retry：DNS / kubectl exec channel / git fetch 抖动
    自动重跑（与 spec_lint 同 flake retry 配置）。
    """
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id)
    log.info(
        "checker.analyze_artifact_check.start",
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
            "checker.analyze_artifact_check.timeout", req_id=req_id,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"analyze artifact check 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
            reason="timeout",
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.analyze_artifact_check.done",
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
