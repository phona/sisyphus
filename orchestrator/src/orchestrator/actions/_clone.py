"""server-side clone helper：start_analyze 系列 action 用，把 ctx 里的
involved_repos 落到 runner pod 的 /workspace/source/<basename>/。

入口：`clone_involved_repos_into_runner(req_id, ctx)`，三种返回：
- (None, None)：runner controller 没就绪 / ctx 没 involved_repos 都返这个
  —— caller 跳过 clone 直接 dispatch agent（直接 analyze 路径兼容）
- (repos, None)：clone 成功，repos 是真跑过 helper 的列表
- (repos, exit_code)：clone 失败，exit_code 是 helper 退码（caller 应
  emit VERIFY_ESCALATE，不打 agent 进空 PVC）
"""
from __future__ import annotations

import shlex

import structlog

from .. import k8s_runner

log = structlog.get_logger(__name__)

_CLONE_HELPER = "/opt/sisyphus/scripts/sisyphus-clone-repos.sh"
_CLONE_TIMEOUT_SEC = 600


def _resolve_repos(ctx: dict | None) -> list[str]:
    """优先级 ctx.intake_finalized_intent.involved_repos > ctx.involved_repos。"""
    if not ctx:
        return []
    finalized = ctx.get("intake_finalized_intent") or {}
    repos = finalized.get("involved_repos") or ctx.get("involved_repos") or []
    return [r for r in repos if isinstance(r, str) and r]


async def clone_involved_repos_into_runner(
    req_id: str, ctx: dict | None,
) -> tuple[list[str] | None, int | None]:
    """在 runner pod 里跑 sisyphus-clone-repos.sh。

    返回 (repos, exit_code)：
    - (None, None)：跳过（无 controller 或无 repos），caller 继续 dispatch agent
    - (repos, None)：成功（helper exit 0）
    - (repos, exit_code)：失败（helper 非 0），caller 应 escalate
    """
    repos = _resolve_repos(ctx)
    if not repos:
        log.info("clone.skip_no_repos", req_id=req_id)
        return None, None

    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        # dev 环境无 K8s：跳过 server-side clone，agent 自己 clone
        log.warning("clone.no_runner_controller", req_id=req_id, error=str(e))
        return None, None

    args = " ".join(shlex.quote(r) for r in repos)
    cmd = f"{_CLONE_HELPER} {args}"
    log.info("clone.exec", req_id=req_id, repos=repos)
    result = await rc.exec_in_runner(req_id, cmd, timeout_sec=_CLONE_TIMEOUT_SEC)

    if result.exit_code != 0:
        log.error(
            "clone.failed", req_id=req_id, repos=repos,
            exit_code=result.exit_code,
            stderr_tail=result.stderr[-512:] if result.stderr else "",
        )
        return repos, result.exit_code

    log.info("clone.done", req_id=req_id, repos=repos,
             duration_sec=round(result.duration_sec, 1))
    return repos, None
