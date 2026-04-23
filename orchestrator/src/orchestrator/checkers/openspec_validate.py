"""openspec 自检（M3）：sisyphus 在 runner pod 直接跑 `openspec validate`，吃退出码定 pass/fail。

spec-agent 写完 openspec/changes/<REQ>/ 下的 spec 文件后，sisyphus 不靠 ci-passed tag，
靠这里 admission gate emit 的事件。两个 spec issue（contract / acceptance）共用本 checker：
传 spec_stage 只是用于打日志和写 artifact_checks 表。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from .. import k8s_runner
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048


def _build_cmd(req_id: str, leader_repo_path: str) -> str:
    """跑 openspec validate <change-id>。

    spec-agent 把文件写在 leader source repo 的 openspec/changes/<REQ>/ 下，
    cwd 切到那个 repo 才能让 openspec 找到 openspec/ 配置。leader_repo_path 由
    caller 传入（M15 砍 manifest 后没有集中存储，按调度上下文传）。
    """
    return (
        f"set -e; cd \"{leader_repo_path}\" && "
        f"openspec validate openspec/changes/{req_id}"
    )


async def run_openspec_validate(
    req_id: str,
    *,
    spec_stage: str,
    leader_repo_path: str,
    timeout_sec: int = 120,
) -> CheckResult:
    """kubectl exec runner -- openspec validate ...，收 stdout/stderr/exit。"""
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id, leader_repo_path)
    log.info(
        "checker.openspec_validate.start",
        req_id=req_id, spec_stage=spec_stage, timeout=timeout_sec,
    )
    started = time.monotonic()

    try:
        exec_result = await asyncio.wait_for(
            rc.exec_in_runner(req_id, cmd, timeout_sec=timeout_sec),
            timeout=timeout_sec + 10,
        )
    except TimeoutError:
        log.error(
            "checker.openspec_validate.timeout", req_id=req_id, spec_stage=spec_stage,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"openspec validate 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.openspec_validate.done",
        req_id=req_id, spec_stage=spec_stage,
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
