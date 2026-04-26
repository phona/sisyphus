"""Admission gate for fresh REQ entry.

Two checks at the door of `start_intake` / `start_analyze` (the two actions
that turn an incoming `intent:*` webhook into a runner Pod + PVC):

1. **In-flight cap** — refuse new work when the count of non-terminal REQs
   would push past `settings.inflight_req_cap`. Without this, a webhook
   burst can pile up enough runner Pods to wedge the K8s scheduler.
2. **Disk pressure** — refuse new work when
   `RunnerController.node_disk_usage_ratio()` is at or above
   `settings.admission_disk_pressure_threshold`. `runner_gc` already
   does an *emergency* purge above 0.8, but it polls on a 15-min loop;
   without this gate, sisyphus keeps creating PVCs in the gap.

Rejection is expressed as `AdmissionDecision(admit=False, reason=...)`. The
caller writes `ctx.escalated_reason` and returns `{"emit": "verify.escalate"}`,
so the existing state-machine path puts the REQ in ESCALATED for human
re-trigger. We deliberately do not auto-retry — that matches the project
philosophy ("sisyphus 不机制性兜 retry") and keeps the gate simple.

Both checks fail open: a DB error or a transient disk probe failure admits
the REQ rather than blocking healthy entries on infra noise.
"""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import structlog
from kubernetes.client import ApiException

from . import k8s_runner, runner_gc
from .config import settings

log = structlog.get_logger(__name__)


# Non-terminal states for the in-flight cap. Excluding `init` keeps brand-new
# rows that haven't dispatched yet from being counted as work-in-progress;
# excluding `gh-incident-open` keeps escalated-but-waiting-for-human REQs out
# of the cap (they consume no runner Pod). Done / escalated are terminal.
_INFLIGHT_EXCLUDE_STATES: tuple[str, ...] = (
    "init", "done", "escalated", "gh-incident-open",
)


@dataclass(frozen=True)
class AdmissionDecision:
    """Result of `check_admission`.

    `admit=True` → caller proceeds with `ensure_runner` + agent dispatch.
    `admit=False` → caller writes `ctx.escalated_reason = reason` and emits
    `VERIFY_ESCALATE`.
    """
    admit: bool
    reason: str | None = None


async def check_admission(
    pool: asyncpg.Pool, *, req_id: str,
) -> AdmissionDecision:
    """Run both gates; first failure short-circuits."""
    cap_decision = await _check_inflight_cap(pool, req_id=req_id)
    if not cap_decision.admit:
        return cap_decision
    return await _check_disk_pressure()


async def _check_inflight_cap(
    pool: asyncpg.Pool, *, req_id: str,
) -> AdmissionDecision:
    cap = settings.inflight_req_cap
    if cap <= 0:
        return AdmissionDecision(admit=True)
    try:
        row = await pool.fetchrow(
            "SELECT COUNT(*)::BIGINT AS n FROM req_state "
            "WHERE state <> ALL($1::text[]) AND req_id <> $2",
            list(_INFLIGHT_EXCLUDE_STATES), req_id,
        )
    except Exception as e:
        log.warning("admission.cap_query_failed", req_id=req_id, error=str(e))
        return AdmissionDecision(admit=True)
    count = int(row["n"]) if row else 0
    if count >= cap:
        reason = f"inflight-cap-exceeded:{count}/{cap}"
        log.warning("admission.cap_rejected", req_id=req_id,
                    inflight=count, cap=cap)
        return AdmissionDecision(admit=False, reason=reason)
    return AdmissionDecision(admit=True)


async def _check_disk_pressure() -> AdmissionDecision:
    threshold = settings.admission_disk_pressure_threshold
    # Reuse the GC's RBAC short-circuit so admission stops probing once the
    # cluster has told us nodes:list is denied.
    if runner_gc._DISK_CHECK_DISABLED:
        return AdmissionDecision(admit=True)
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError:
        # No controller (dev / unit test). Fail open.
        return AdmissionDecision(admit=True)
    try:
        ratio = await rc.node_disk_usage_ratio()
    except ApiException as e:
        if e.status == 403:
            runner_gc._DISK_CHECK_DISABLED = True
            log.info("admission.disk_check_rbac_denied",
                     hint="ServiceAccount lacks cluster-scoped nodes:list; "
                          "admission disk gate disabled until restart")
        else:
            log.debug("admission.disk_check_failed",
                      error=str(e), status=e.status)
        return AdmissionDecision(admit=True)
    except Exception as e:
        log.debug("admission.disk_check_failed", error=str(e))
        return AdmissionDecision(admit=True)
    if ratio >= threshold:
        reason = f"disk-pressure:{ratio:.2f}/{threshold:.2f}"
        log.warning("admission.disk_rejected",
                    ratio=round(ratio, 2), threshold=threshold)
        return AdmissionDecision(admit=False, reason=reason)
    return AdmissionDecision(admit=True)
