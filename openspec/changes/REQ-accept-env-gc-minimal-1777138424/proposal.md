# Proposal: minimal accept_env_gc skeleton

## Problem

`teardown_accept_env` runs `make accept-env-down` best-effort after every accept stage (pass or fail). If it fails, the accept environment — a Kubernetes namespace `accept-{req_id.lower()}` containing helm releases and all their resources — is left behind permanently. There is no background cleanup, so orphaned namespaces accumulate over time and consume cluster resources.

## Solution

Add `accept_env_gc` as a background GC loop (parallel to `runner_gc`) that:

1. Lists all Kubernetes namespaces matching the `accept-req-*` pattern
2. Queries `req_state` for terminal REQs (done / escalated past retention)
3. Deletes orphaned accept namespaces via `kubectl delete namespace` (cascades to all resources inside)

**"No helm RBAC"**: we do not call `helm uninstall`. Namespace deletion is sufficient and requires only `namespaces:delete` (namespace-scoped is sufficient once we have the name). The RBAC requirement for `namespaces:list` is cluster-scoped; if the ServiceAccount lacks it, we disable the GC gracefully with a single INFO log (same pattern as `runner_gc._DISK_CHECK_DISABLED`).

## Scope (minimal skeleton)

- New module: `orchestrator/src/orchestrator/accept_env_gc.py`
- Config: `accept_env_gc_interval_sec` (default 900s, same as `runner_gc_interval_sec`)
- Wire into `main.py` startup
- Unit tests (no K8s integration tests)

Retention policy reuses `pvc_retain_on_escalate_days` (done → immediate, escalated → wait retention before GC).
