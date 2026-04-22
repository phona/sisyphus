"""manifest_io：从 runner PVC 读 /workspace/.sisyphus/manifest.yaml。

M11 引入：staging-test / pr-ci-watch checker 需要 analyze agent 写的配置
（test.cmd / pr.repo 等），不再硬编码。admission 已强制 schema 带 test/pr
必填字段，所以 checker 阶段能读到就一定有这俩 key。

分两步：
- kubectl exec cat → bytes
- yaml.safe_load → dict

失败一律 raise ManifestReadError，让 checker 决定怎么收（通常转 flaky 退
回 retry.executor）。
"""
from __future__ import annotations

import asyncio

import structlog
import yaml

from .. import k8s_runner

log = structlog.get_logger(__name__)

MANIFEST_PATH = "/workspace/.sisyphus/manifest.yaml"
_READ_CMD = f"cat {MANIFEST_PATH}"


class ManifestReadError(RuntimeError):
    """读 manifest 任何一步失败都抛这个。"""


async def read_manifest(req_id: str, *, timeout_sec: int = 30) -> dict:
    """kubectl exec runner cat manifest.yaml → parse YAML → dict。

    任一环节失败（pod 不可达 / cat 非 0 / yaml 坏）抛 ManifestReadError。
    """
    rc = k8s_runner.get_controller()

    try:
        exec_result = await asyncio.wait_for(
            rc.exec_in_runner(req_id, _READ_CMD, timeout_sec=timeout_sec),
            timeout=timeout_sec + 10,
        )
    except TimeoutError as e:
        raise ManifestReadError(f"read manifest timeout after {timeout_sec}s") from e

    if exec_result.exit_code != 0:
        raise ManifestReadError(
            f"cat {MANIFEST_PATH} exit {exec_result.exit_code}: "
            f"{(exec_result.stderr or '').strip()[:200]}"
        )

    try:
        data = yaml.safe_load(exec_result.stdout)
    except yaml.YAMLError as e:
        raise ManifestReadError(f"yaml parse failed: {e}") from e

    if not isinstance(data, dict):
        raise ManifestReadError(f"manifest root must be object, got {type(data).__name__}")

    log.debug("checker.manifest_io.read_ok", req_id=req_id, keys=sorted(data.keys()))
    return data
