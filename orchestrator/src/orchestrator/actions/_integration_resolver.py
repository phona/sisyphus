"""Resolve where `make accept-env-{up,down}` should run inside the runner pod.

Background (REQ-self-accept-stage-1777121797): the historical accept stage assumed
a separate integration repo cloned into `/workspace/integration/<basename>` (the
`phona/ttpos-arch-lab` model). Sisyphus self-dogfood — and any single-repo
deployment — has no such standalone integration repo: the source repo IS the
integration repo (top-level Makefile carries `accept-env-up:`).

This helper centralizes resolution so create_accept and teardown_accept_env stay
in sync:

    1. If any /workspace/integration/<name>/Makefile carries `accept-env-up:`
       → use it (multi-repo / external-integration-repo path; preserves prior
       behavior).
    2. Else if EXACTLY ONE /workspace/source/<name>/Makefile carries
       `accept-env-up:` → use it (self-host fallback).
    3. Else → return ResolveResult(dir=None, reason=...) so the caller can
       emit a friendly accept-env-up.fail (no shell glob explosion on empty
       /workspace/integration/*).

Multiple source candidates → no fallback (refuse to silently pick a leader);
the caller must surface the error and the human must explicitly stage an
integration repo.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)


# 单 shell 调用扫两个 root，每个候选 dir 一行，前缀 I:/S: 标识来源。
# 用 `make -p -n` 解析 Makefile + include 子 mk（实证 ttpos-server-go：
# accept-env-up 在 ttpos-scripts/accept-env.mk via include，顶层 grep 漏判 →
# resolver 误判"无 integration repo"），跟 dev_cross_check / staging_test 同根因。
_SCAN_SCRIPT = r"""
set +e
for d in /workspace/integration/*/; do
  [ -d "$d" ] || continue
  [ -f "${d}Makefile" ] || continue
  if (cd "$d" && make -p -n 2>/dev/null | grep -qE '^accept-env-up:'); then
    printf 'I:%s\n' "${d%/}"
  fi
done
for d in /workspace/source/*/; do
  [ -d "$d" ] || continue
  [ -f "${d}Makefile" ] || continue
  if (cd "$d" && make -p -n 2>/dev/null | grep -qE '^accept-env-up:'); then
    printf 'S:%s\n' "${d%/}"
  fi
done
exit 0
""".strip()


@dataclass(frozen=True)
class ResolveResult:
    """Result of resolving the integration directory.

    `dir` is the absolute runner-pod path to cd into for `make accept-env-*`,
    or None if no suitable directory could be picked. When None, `reason` carries
    a human-readable description for the caller to log + propagate.
    """
    dir: str | None
    reason: str = ""


def _parse_scan(stdout: str) -> tuple[list[str], list[str]]:
    """Return (integration_dirs, source_dirs) parsed from _SCAN_SCRIPT output."""
    integ, src = [], []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("I:"):
            integ.append(line[2:])
        elif line.startswith("S:"):
            src.append(line[2:])
    return integ, src


def _decide(integ: list[str], src: list[str]) -> ResolveResult:
    """Apply the resolution policy to scanned candidates."""
    if integ:
        # 多个 integration 候选时取首个（M16 不强假设；和原 `cd /workspace/integration/*`
        # glob 行为等价 —— shell 也按字典序拿一个）
        return ResolveResult(dir=integ[0])
    if len(src) == 1:
        return ResolveResult(dir=src[0])
    if len(src) == 0:
        return ResolveResult(
            dir=None,
            reason="no integration dir resolvable: /workspace/integration/* and "
            "/workspace/source/*/Makefile both lack accept-env-up target",
        )
    return ResolveResult(
        dir=None,
        reason=(
            f"no integration dir resolvable: multiple source candidates carry "
            f"accept-env-up target ({', '.join(src)}); refuse to pick one — "
            "stage an explicit integration repo under /workspace/integration/"
        ),
    )


async def resolve_integration_dir(rc, req_id: str) -> ResolveResult:
    """Discover where to run `make accept-env-{up,down}` for this REQ.

    `rc` is a k8s_runner.RunnerController (or test double exposing
    `exec_in_runner`). The function performs one `kubectl exec` round-trip —
    callers should cache the result if they need it twice (env-up + env-down
    are separate stage transitions, so they re-resolve independently; that's
    intentional, the workspace shape can change between them in theory).
    """
    result = await rc.exec_in_runner(
        req_id,
        command=_SCAN_SCRIPT,
        env={"SISYPHUS_STAGE": "accept-resolve"},
        timeout_sec=15,
    )
    if result.exit_code != 0:
        # Scanner shouldn't fail (set +e + exit 0), but be defensive.
        log.warning(
            "integration_resolver.scan_nonzero",
            req_id=req_id, exit_code=result.exit_code,
            stderr_tail=result.stderr[-200:],
        )
        return ResolveResult(
            dir=None,
            reason=f"scan exec returned exit_code={result.exit_code}",
        )
    integ, src = _parse_scan(result.stdout)
    decision = _decide(integ, src)
    log.info(
        "integration_resolver.decided",
        req_id=req_id,
        integration_candidates=integ, source_candidates=src,
        chosen=decision.dir, reason=decision.reason or None,
    )
    return decision
