# runner-gc-pod-pvc-split

## ADDED Requirements

### Requirement: runner_gc.gc_once SHALL compute Pod and PVC keep sets independently and dispatch each to its own controller sweep

The orchestrator SHALL split the single `_active_req_ids` keep set into two
keep sets that are computed independently from the same `req_state` snapshot:
a Pod keep set containing only REQs whose `state` is non-terminal (i.e.
NOT in `{done, escalated}`), and a PVC keep set containing non-terminal
REQs PLUS `escalated` REQs whose `updated_at` is within
`settings.pvc_retain_on_escalate_days`. `gc_once` MUST then dispatch the
Pod keep set to `RunnerController.gc_orphan_pods(...)` and the PVC keep
set to `RunnerController.gc_orphan_pvcs(...)`. The function MUST return a
dict containing both `cleaned_pods` and `cleaned_pvcs` (lists of REQ ids
that were swept), `pod_kept` and `pvc_kept` (cardinalities of the two
keep sets), and the existing `disk_pressure` boolean. The Pod keep set
MUST NOT honor any retention window for `escalated` REQs because the Pod
holds 512Mi memory request / 8Gi limit and that capacity is needed by
in-flight REQs; only the PVC retention window is for the human-debug
workflow.

#### Scenario: RGS-S1 escalated REQ within retention is in PVC keep set but NOT in Pod keep set

- **GIVEN** a `req_state` row with `state='escalated'` and `updated_at` set to
  2 hours ago (well within the default `pvc_retain_on_escalate_days=1` window)
  and the K8s runner controller initialized with stub
  `gc_orphan_pods` / `gc_orphan_pvcs` methods that return `[]`
- **WHEN** `runner_gc.gc_once()` is awaited
- **THEN** `RunnerController.gc_orphan_pods` MUST be invoked exactly once
  with a keep set that does NOT contain that REQ id (so its zombie Pod
  would be reaped)
- **AND** `RunnerController.gc_orphan_pvcs` MUST be invoked exactly once
  with a keep set that DOES contain that REQ id (so its PVC is preserved
  for human debug)
- **AND** the returned dict MUST contain both `cleaned_pods` and
  `cleaned_pvcs` keys

#### Scenario: RGS-S2 disk pressure forces escalated PVC out of keep set; Pod keep set still excludes terminal states

- **GIVEN** a `req_state` row with `state='escalated'` and `updated_at` set
  to 2 hours ago, AND `node_disk_usage_ratio()` returning 0.9 (above the
  default 0.8 threshold)
- **WHEN** `runner_gc.gc_once()` is awaited
- **THEN** `RunnerController.gc_orphan_pvcs` MUST be invoked with an
  empty keep set (escalated retention waived under disk pressure)
- **AND** `RunnerController.gc_orphan_pods` MUST be invoked with an empty
  keep set (Pod keep set never includes terminal states)
- **AND** the returned dict MUST contain `disk_pressure == True`

#### Scenario: RGS-S3 in-flight REQs are in both keep sets; done REQs are in neither

- **GIVEN** three `req_state` rows: `REQ-A` state `analyzing`, `REQ-B`
  state `staging-test-running`, `REQ-C` state `done` with recent
  `updated_at`
- **WHEN** `runner_gc.gc_once()` is awaited
- **THEN** `RunnerController.gc_orphan_pods` MUST be invoked with a keep
  set equal to `{"REQ-A", "REQ-B"}`
- **AND** `RunnerController.gc_orphan_pvcs` MUST be invoked with a keep
  set equal to `{"REQ-A", "REQ-B"}`
- **AND** `REQ-C` MUST appear in neither keep set

### Requirement: RunnerController.gc_orphan_pods SHALL list runner Pods by label and delete only Pods whose REQ is not in the keep set

The orchestrator SHALL expose `RunnerController.gc_orphan_pods(keep_req_ids:
set[str]) -> list[str]` that lists Pods in the runner namespace by label
selector `sisyphus/role=runner`, extracts the REQ id from the
`sisyphus/req-id` label, and deletes each Pod whose REQ id (case-folded)
is NOT present in `keep_req_ids` (also case-folded). The method MUST NOT
delete or otherwise mutate any PVC. The method MUST handle 404 from the
delete call as a no-op (Pod already gone). The returned list MUST contain
every REQ id that the method attempted to delete. This sweep covers Pods
that survived `_cleanup_runner_on_terminal`'s fire-and-forget cleanup
because of K8s API blips, orchestrator restart eating the task before
completion, or manual `kubectl apply` re-creating the Pod after escalate.

#### Scenario: RGS-S4 gc_orphan_pods deletes Pods not in keep set, leaves PVCs alone

- **GIVEN** the K8s mock controller has three runner Pods labeled with
  `sisyphus/req-id=req-1`, `req-2`, `req-3` and `sisyphus/role=runner`,
  AND `delete_namespaced_pod` returns success for all three
- **WHEN** `RunnerController.gc_orphan_pods({"REQ-1"})` is awaited
- **THEN** `delete_namespaced_pod` MUST be invoked exactly twice (once
  for `REQ-2`, once for `REQ-3`)
- **AND** `delete_namespaced_persistent_volume_claim` MUST NOT be
  invoked
- **AND** the returned list MUST contain exactly `REQ-2` and `REQ-3`

### Requirement: RunnerController.gc_orphan_pvcs SHALL list runner PVCs by label and delete only PVCs whose REQ is not in the keep set

The orchestrator SHALL expose `RunnerController.gc_orphan_pvcs(keep_req_ids:
set[str]) -> list[str]` that lists PVCs in the runner namespace by label
selector `sisyphus/role=workspace`, extracts the REQ id from the
`sisyphus/req-id` label, and deletes each PVC whose REQ id (case-folded)
is NOT present in `keep_req_ids` (also case-folded). The method MUST NOT
delete or otherwise mutate any Pod (Pod GC is a separate concern handled
by `gc_orphan_pods`). The method MUST handle 404 from the delete call as
a no-op (PVC already gone). The returned list MUST contain every REQ id
that the method attempted to delete. If a PVC still has a Pod attached
when the delete request is issued, K8s SHALL accept the delete and mark
the PVC as `Terminating`; the actual deletion completes after the next
`gc_orphan_pods` sweep removes the Pod.

#### Scenario: RGS-S5 gc_orphan_pvcs deletes PVCs not in keep set, leaves Pods alone

- **GIVEN** the K8s mock controller has three workspace PVCs labeled with
  `sisyphus/req-id=req-1`, `req-2`, `req-3` and `sisyphus/role=workspace`,
  AND `delete_namespaced_persistent_volume_claim` returns success for all
- **WHEN** `RunnerController.gc_orphan_pvcs({"REQ-1"})` is awaited
- **THEN** `delete_namespaced_persistent_volume_claim` MUST be invoked
  exactly twice (once for `REQ-2`, once for `REQ-3`)
- **AND** `delete_namespaced_pod` MUST NOT be invoked
- **AND** the returned list MUST contain exactly `REQ-2` and `REQ-3`

### Requirement: gc_once preserves the disk-check RBAC degradation contract from REQ-orch-noise-cleanup

The split into Pod / PVC sweeps MUST NOT change any of the existing
disk-check side effects exercised by ORCHN-S4..S8 contracts: the first
`ApiException(status=403)` from `node_disk_usage_ratio()` MUST set the
process-level `_DISK_CHECK_DISABLED` flag and emit exactly one INFO log
`runner_gc.disk_check_rbac_denied`; subsequent ticks MUST short-circuit
the disk probe; non-403 `ApiException` MUST log DEBUG
`runner_gc.disk_check_failed` and leave the flag False; ratio above
threshold MUST emit WARNING `runner_gc.disk_pressure` and set
`disk_pressure=True` in the result. The sweep dispatch ordering (disk
probe first, then keep-set computation, then Pod sweep, then PVC sweep)
MUST be preserved so that a disk-pressure tick can still ignore the
escalated PVC retention.

#### Scenario: RGS-S6 gc_once with disk-check 403 sets flag and still computes both keep sets

- **GIVEN** `_DISK_CHECK_DISABLED` is False AND `node_disk_usage_ratio()`
  raises `ApiException(status=403)` AND a single `req_state` row with
  `state='analyzing'`
- **WHEN** `runner_gc.gc_once()` is awaited
- **THEN** the result dict MUST contain `disk_pressure == False`
- **AND** `_DISK_CHECK_DISABLED` MUST be True
- **AND** exactly one INFO log `runner_gc.disk_check_rbac_denied` MUST be
  emitted (no WARNING-level instance of that event)
- **AND** both `RunnerController.gc_orphan_pods` and
  `RunnerController.gc_orphan_pvcs` MUST be invoked exactly once with
  keep sets containing the analyzing REQ id
