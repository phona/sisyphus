"""Runner pod self-heal helper (REQ-fix-runner-self-heal-394, closes #394).

Background: 2026-05-04 v5 dogfood staging_test 卡：earlier ops session 跑过
`kubectl delete pod --all -n sisyphus-runners`（清 secret cache）误删了 per-REQ
runner pod；PVC 还在但 orchestrator 假设 pod 一直 alive，下一次 stage 推进时
checker 直接 exec 收到 404 → fail → escalate。

`ensure_runner_alive` 是一个轻量幂等的 ping：alive 直接返；NotFound 走
`RunnerController.ensure_runner(wait_ready=True)` lazy 重建，PVC 复用
（`/workspace` 内容、clone、go cache 不丢）。Failed/Succeeded 这种 terminal
pod 先 `pause` 删旧 pod 再 ensure_runner，避免 create 撞 409。

仅给 *复用* runner 的 stage（机械 checker / 不进新 stage 的 action）调；
`start_*` 系列已经显式 `ensure_runner(wait_ready=True)`，不需要再包一层。
"""
from __future__ import annotations

import structlog

from .. import k8s_runner

log = structlog.get_logger(__name__)

# 终态 pod 仍占 API name（同名 create 撞 409）—— 必须先删再建。
_TERMINAL_PHASES = frozenset({"Failed", "Succeeded"})

# 这些 phase 视为 "alive enough"；Pending/Unknown 也跳过自愈，让 caller 的 exec
# 自然 retry / timeout（kubernetes-python stream 在 pod 没 Running 时会自己等
# 或抛清晰错误，比这里盲删 pod 安全）。
_ALIVE_PHASES = frozenset({"Pending", "Running", "Unknown"})


async def ensure_runner_alive(req_id: str) -> bool:
    """Verify the per-REQ runner pod is alive; lazy recreate (PVC reused) if missing.

    Returns:
        True  — pod is alive (or was just recreated successfully).
        False — `RunnerController` 未初始化（dev / local 无 K8s）。caller 决定要不要
                继续；现有 caller 全是"无 controller 直接 warning + skip"语义。
    """
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("runner.alive.no_controller", req_id=req_id, error=str(e))
        return False

    status = await rc.get_runner_status(req_id)
    pod_phase = status.pod_phase if status is not None else "NotFound"
    pvc_phase = status.pvc_phase if status is not None else "NotFound"

    if status is not None and pod_phase in _ALIVE_PHASES:
        return True

    log.info(
        "runner.lazy_recreate",
        req_id=req_id,
        prev_pod_phase=pod_phase,
        prev_pvc_phase=pvc_phase,
    )

    if pod_phase in _TERMINAL_PHASES:
        # delete pod (PVC kept) before re-create to avoid 409 on same name
        await rc.pause(req_id)

    await rc.ensure_runner(req_id, wait_ready=True)
    return True
