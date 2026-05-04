# Spec delta — runner-pod-self-heal

## ADDED Requirements

### Requirement: orchestrator self-heals missing runner pod via lazy recreate

Reuse-runner stages (机械 checker 与不创建新 stage 的 action) MUST verify the
per-REQ runner pod is alive before issuing `exec_in_runner`, and SHALL lazy
recreate the pod (re-binding the existing per-REQ PVC) when it is missing.

The orchestrator SHALL expose
`orchestrator.actions._runner.ensure_runner_alive(req_id) -> bool` with the
following contract:

1. Reads pod status via `RunnerController.get_runner_status(req_id)`.
2. If status is non-None and `pod_phase` is one of `Pending` / `Running` /
   `Unknown`, the function MUST return `True` without invoking
   `ensure_runner` (cheap fast path).
3. If status is `None` or `pod_phase` is `NotFound`, the function MUST call
   `RunnerController.ensure_runner(req_id, wait_ready=True)` and return `True`
   on success. The PVC is reused: `ensure_runner`'s `create_namespaced_pvc`
   path MUST be tolerated as 409 Conflict (existing PVC), so `/workspace`
   contents (clones, go cache, intermediate artifacts) survive across the
   pod recreate.
4. If `pod_phase` is `Failed` or `Succeeded` (terminal), the function MUST
   first invoke `RunnerController.pause(req_id)` (delete pod, keep PVC),
   then call `ensure_runner(req_id, wait_ready=True)`. This avoids the
   409-on-create that would otherwise occur because the terminal pod object
   still occupies the API name.
5. If `RunnerController` is not initialized (dev / local without K8s),
   `ensure_runner_alive` MUST log a warning and return `False`. Callers
   SHALL treat `False` as "skip self-heal" rather than aborting (preserves
   the dev-time "no controller" behaviour already present elsewhere in
   the actions/checkers code).

The following entry points MUST call `ensure_runner_alive(req_id)` before
their first `exec_in_runner` invocation:

- `orchestrator.checkers.spec_lint.run_spec_lint`
- `orchestrator.checkers.dev_cross_check.run_dev_cross_check`
- `orchestrator.checkers.staging_test.run_staging_test`
- `orchestrator.checkers.analyze_artifact_check.run_analyze_artifact_check`
- `orchestrator.actions.create_pr_ci_watch._discover_repos_from_runner`
- `orchestrator.actions.teardown_accept_env._run_single_layer_teardown`
- `orchestrator.actions.teardown_accept_env._run_multi_layer_teardown`

`start_intake` / `start_analyze` / `start_analyze_with_finalized_intent` /
`start_challenger` already invoke `ensure_runner(wait_ready=True)` directly
and SHALL remain unchanged. `create_accept._ensure_runner_pod_ready` already
performs lazy ensure-and-clone and SHALL remain unchanged.

A `runner.lazy_recreate` structlog event MUST be emitted whenever a recreate
path runs, carrying `req_id`, `prev_pod_phase`, and `prev_pvc_phase` fields,
so observability can count self-heal frequency without schema changes.

#### Scenario: RSH-S1 alive pod is a no-op fast path
- **GIVEN** a runner pod for `req_id="REQ-X"` whose `get_runner_status` returns
  a `RunnerStatus` with `pod_phase="Running"`
- **WHEN** `ensure_runner_alive("REQ-X")` is awaited
- **THEN** it returns `True` and `RunnerController.ensure_runner` is NOT called
- **AND** no `runner.lazy_recreate` event is emitted

#### Scenario: RSH-S2 missing pod triggers lazy recreate with PVC reuse
- **GIVEN** a runner whose `get_runner_status` returns `None` (both pod and
  PVC NotFound), or returns a `RunnerStatus` with `pod_phase="NotFound"`
- **WHEN** `ensure_runner_alive("REQ-X")` is awaited
- **THEN** `RunnerController.ensure_runner("REQ-X", wait_ready=True)` MUST be
  called exactly once
- **AND** the function returns `True`
- **AND** a `runner.lazy_recreate` event is emitted with the prior pod phase

#### Scenario: RSH-S3 terminal pod is paused before recreate
- **GIVEN** a runner whose `get_runner_status` returns `pod_phase="Failed"`
- **WHEN** `ensure_runner_alive("REQ-X")` is awaited
- **THEN** `RunnerController.pause("REQ-X")` MUST be called before
  `ensure_runner(...)`
- **AND** `ensure_runner("REQ-X", wait_ready=True)` is then called
- **AND** the function returns `True`

#### Scenario: RSH-S4 missing controller returns False without raising
- **GIVEN** `k8s_runner.get_controller()` raises `RuntimeError`
  (controller not initialised)
- **WHEN** `ensure_runner_alive("REQ-X")` is awaited
- **THEN** the function MUST return `False`
- **AND** MUST NOT raise; the caller decides whether to proceed (existing
  callers already log + skip in the no-controller branch)
