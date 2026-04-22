"""staging-test 自检（M1 + M11）：sisyphus 在 runner pod 直接跑测试命令，收退出码决定 pass/fail。

不起 BKD agent，不靠 result:pass tag，sisyphus 是唯一裁判。

M11：test_cmd / cwd / timeout 从 /workspace/.sisyphus/manifest.yaml 的 `test` 段读，
不再硬编码。admission 已强制 manifest 带 test 段，checker 跑到这里一定能读到。
"""
from __future__ import annotations

import asyncio

import structlog

from .. import k8s_runner
from . import manifest_io
from ._types import CheckResult

__all__ = ["CheckResult", "run_staging_test"]

log = structlog.get_logger(__name__)

_TAIL = 2048  # stdout/stderr 各截尾 2KB，防 OOM
_DEFAULT_TIMEOUT = 600


async def run_staging_test(req_id: str) -> CheckResult:
    """读 manifest → 拼 `cd <cwd> && <cmd>` → 在 runner pod 里执行 → 收集结果。

    Raises:
        manifest_io.ManifestReadError: 读 manifest 失败（PVC 挂了 / yaml 坏 / 缺字段）
        TimeoutError: 测试超过 timeout_sec
    """
    manifest = await manifest_io.read_manifest(req_id)

    test = manifest.get("test")
    if not isinstance(test, dict):
        raise manifest_io.ManifestReadError("manifest 缺 test 段（admission 应已挡）")

    cmd = test.get("cmd")
    cwd = test.get("cwd")
    if not cmd or not cwd:
        raise manifest_io.ManifestReadError(
            f"manifest.test 缺字段：cmd={cmd!r} cwd={cwd!r}"
        )

    timeout_sec = int(test.get("timeout_sec", _DEFAULT_TIMEOUT))
    final_cmd = f"cd /workspace/{cwd} && {cmd}"

    rc = k8s_runner.get_controller()
    log.info(
        "checker.staging_test.start",
        req_id=req_id, cmd=final_cmd, timeout=timeout_sec,
    )

    result = await asyncio.wait_for(
        rc.exec_in_runner(req_id, final_cmd, timeout_sec=timeout_sec),
        timeout=timeout_sec + 10,  # 比内部 deadline 多 10s，确保内部先超时返 -1
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
        cmd=final_cmd,
    )
