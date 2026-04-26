# feat(orch): in-flight REQ cap + disk pressure precheck

## Why

Sisyphus today admits every `intent:intake` / `intent:analyze` webhook into a
running REQ unconditionally. Two failure modes follow on the single-node K3s
deployment (vm-node04, ~6 GiB RAM, ~50 GB ephemeral):

1. **Concurrent runner storm.** A burst of new REQs (e.g. an analyze-agent
   creating multiple sub-issues, or a user pasting a batch of intents) creates
   a per-REQ runner Pod for each. Each Pod requests `512Mi` and limits to
   `8Gi`; the K8s scheduler eventually `FailedScheduling: Insufficient memory`
   and the pile-up wedges existing in-flight REQs.
2. **PVC creation on a full disk.** `runner_gc.gc_once` already does an
   *emergency* purge when the node crosses
   `runner_gc_disk_pressure_threshold` (default `0.8`), but it runs on a
   15-minute interval (`runner_gc_interval_sec`). Between ticks, sisyphus
   happily creates more `workspace-<req-id>` PVCs and the node tips into
   `DiskPressure` eviction, killing healthy runners.

Both problems share the same root: there is no admission gate. The
orchestrator never asks "should I take this work right now?" — it just
ensures the runner Pod and dispatches the BKD agent.

## What Changes

Add a single admission gate that runs at the **fresh-entry actions only**
(`start_intake`, `start_analyze`). When a check fails, the action emits
`VERIFY_ESCALATE` with a specific `escalated_reason`, and the existing
state-machine path puts the REQ into `ESCALATED` so a human can re-trigger
once capacity frees up. We do not retry automatically — that matches the
project philosophy ("sisyphus 不机制性兜 retry"), keeps the gate simple,
and makes the rejection visible on the BKD board.

- **New module** `orchestrator/src/orchestrator/admission.py` exposing one
  async function `check_admission(pool, *, req_id) -> AdmissionDecision`.
  - Counts non-terminal REQs in `req_state` (`state NOT IN
    ('init','done','escalated','gh-incident-open') AND req_id <> $1`) and
    rejects when `count >= settings.inflight_req_cap`.
  - Asks `RunnerController.node_disk_usage_ratio()` and rejects when
    `ratio >= settings.admission_disk_pressure_threshold`. Reuses the
    existing `runner_gc._DISK_CHECK_DISABLED` short-circuit so RBAC-denied
    clusters are not re-probed forever; any other exception is treated as
    fail-open (admit + log).
  - Both checks are independent: a failure short-circuits, but an
    individually-disabled check (`cap == 0` or disk-check off) does not
    block admission.

- **`orchestrator/src/orchestrator/config.py`** adds two settings:
  - `inflight_req_cap: int = 10` — `0` disables the cap entirely.
  - `admission_disk_pressure_threshold: float = 0.75` — set lower than the
    GC's `0.8` so admission trips first and we stop accepting new work
    before the GC starts evicting PVCs.

- **`orchestrator/src/orchestrator/actions/start_intake.py`** and
  **`start_analyze.py`** — at the very top, before `ensure_runner` /
  `ensure_runner` + clone, call `check_admission`. On rejection set
  `ctx.escalated_reason` (`rate-limit:inflight-cap-exceeded` or
  `rate-limit:disk-pressure`) and return `{"emit": "verify.escalate",
  "reason": "..."}`. `start_analyze_with_finalized_intent` is **not**
  gated — it is a continuation of an already-admitted intake REQ; gating
  it would punish a REQ for capacity arriving between intake and analyze.

- **Tests**:
  - new `orchestrator/tests/test_admission.py` covering the decision
    function (cap on/off, count just below / at / above cap, disk under /
    over threshold, disk RBAC-denied, no-controller in dev).
  - `orchestrator/tests/test_actions_start_analyze.py` gains a denial test
    that locks in the escalate emit for `start_analyze`.
  - `orchestrator/tests/test_intake.py` gains the same for `start_intake`.

## Impact

- **Affected specs**: new capability `orch-rate-limit` (purely additive).
- **Affected code**: `orchestrator/src/orchestrator/admission.py` (new),
  `config.py`, `actions/start_intake.py`, `actions/start_analyze.py`, the
  three test modules above.
- **Deployment / migration**: zero ops — orchestrator rollout-restart picks
  up the new module. Defaults (`cap=10`, `disk=0.75`) are chosen so an
  existing healthy cluster sees no behavior change. Operators who want a
  different policy override via env (`SISYPHUS_INFLIGHT_REQ_CAP`,
  `SISYPHUS_ADMISSION_DISK_PRESSURE_THRESHOLD`) or helm values.
- **Risk**: low. Both checks fail-open on infrastructure errors (DB query
  fails → admit; disk probe raises non-403 → admit). The strictest path
  (DB succeeds + disk probe succeeds + above threshold) escalates the new
  REQ with a clear reason tag, which is how we already handle "this REQ
  can't proceed right now".
- **Out of scope**: queueing rejected REQs for later auto-retry,
  per-project quotas, weighted admission (e.g. cap excludes
  `REVIEW_RUNNING`). All deferred — the simple "escalate + human
  re-trigger" loop is the project's standing answer to capacity-class
  problems.
