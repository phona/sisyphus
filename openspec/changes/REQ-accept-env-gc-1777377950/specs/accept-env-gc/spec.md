# accept-env-gc

## ADDED Requirements

### Requirement: accept_env_gc.gc_once SHALL scan all `accept-req-*` namespaces and delete those whose REQ is in a terminal state or absent from req_state

The orchestrator SHALL expose `accept_env_gc.gc_once()` that reads all rows from
`req_state`, computes a keep set of REQ ids whose `state` is NOT in
`{done, escalated}` (non-terminal), lists all K8s namespaces matching
`accept-req-*` via `RunnerController.list_accept_env_namespaces()`, and deletes
(via `RunnerController.delete_namespace()`) every namespace whose extracted REQ id
is NOT in the keep set. A namespace whose REQ id cannot be found in `req_state`
(orphan) SHALL also be deleted. The function MUST handle `ApiException(status=404)`
from the delete call as a success (namespace already gone). The function MUST
return a dict containing `cleaned_namespaces` (list of deleted ns names),
`kept_namespaces` (list of retained ns names), `cleaned_count`, `kept_count`, and
`ran_at` (ISO timestamp). The function MUST update the module-level
`_last_gc_result` on every invocation, including skipped ones.

#### Scenario: AEGC-S1 active REQ keeps its accept namespace

- **GIVEN** `req_state` rows `REQ-1` state `accept-running` and `REQ-2` state
  `analyzing`, AND the K8s controller lists namespaces `["accept-req-1",
  "accept-req-2"]`
- **WHEN** `accept_env_gc.gc_once()` is awaited
- **THEN** both namespaces MUST be in `kept_namespaces`
- **AND** `cleaned_namespaces` MUST be empty
- **AND** `delete_namespace` MUST NOT be invoked

#### Scenario: AEGC-S2 done REQ causes namespace deletion

- **GIVEN** a `req_state` row `REQ-1` state `done`, AND the K8s controller lists
  namespace `["accept-req-1"]`
- **WHEN** `accept_env_gc.gc_once()` is awaited
- **THEN** `delete_namespace("accept-req-1")` MUST be invoked exactly once
- **AND** `cleaned_namespaces` MUST equal `["accept-req-1"]`
- **AND** `kept_namespaces` MUST be empty

#### Scenario: AEGC-S3 escalated REQ causes namespace deletion with no retention

- **GIVEN** a `req_state` row `REQ-1` state `escalated` with `updated_at` within
  the default runner PVC retention window, AND the K8s controller lists namespace
  `["accept-req-1"]`
- **WHEN** `accept_env_gc.gc_once()` is awaited
- **THEN** `delete_namespace("accept-req-1")` MUST be invoked exactly once
- **AND** the namespace MUST be in `cleaned_namespaces` (NOT `kept_namespaces`)
- **AND** the behavior MUST differ from runner_gc PVC retention, which would keep
  the PVC for human debug; accept env namespace has no retention concept

#### Scenario: AEGC-S4 orphan namespace (no req_state row) is cleaned

- **GIVEN** `req_state` has only `REQ-1` state `analyzing`, AND the K8s controller
  lists namespaces `["accept-req-1", "accept-req-orphan"]`
- **WHEN** `accept_env_gc.gc_once()` is awaited
- **THEN** `"accept-req-1"` MUST be in `kept_namespaces`
- **AND** `"accept-req-orphan"` MUST be in `cleaned_namespaces`
- **AND** `delete_namespace` MUST be invoked exactly once with
  `"accept-req-orphan"`

#### Scenario: AEGC-S5 empty namespace list is a no-op

- **GIVEN** any `req_state` contents, AND the K8s controller returns an empty
  namespace list
- **WHEN** `accept_env_gc.gc_once()` is awaited
- **THEN** both `cleaned_namespaces` and `kept_namespaces` MUST be empty
- **AND** `delete_namespace` MUST NOT be invoked

#### Scenario: AEGC-S6 delete_namespace 404 counts as cleaned

- **GIVEN** a `req_state` row `REQ-1` state `done`, AND the K8s controller lists
  namespace `["accept-req-1"]`, AND `delete_namespace` raises
  `ApiException(status=404)`
- **WHEN** `accept_env_gc.gc_once()` is awaited
- **THEN** the exception MUST be caught and swallowed
- **AND** `"accept-req-1"` MUST be in `cleaned_namespaces`
- **AND** no exception MUST propagate to the caller

### Requirement: RunnerController.list_accept_env_namespaces SHALL list accept env namespaces by label with prefix fallback

The orchestrator SHALL expose `RunnerController.list_accept_env_namespaces() ->
list[str]` that queries the K8s API for namespaces. It MUST first attempt to
filter by label selector `sisyphus/role=accept-env`. If that returns a non-empty
list or raises an unexpected error, it MUST return those results. If the label
filter returns an empty list, it MUST fall back to listing all namespaces and
filtering by the name prefix `accept-req-` (case-insensitive). The method MUST
return only namespace names (strings), not full V1Namespace objects.

#### Scenario: AEGC-S7 label selector returns matching namespaces

- **GIVEN** the K8s API returns namespaces `["accept-req-1", "accept-req-2"]`
  when queried with label selector `sisyphus/role=accept-env`
- **WHEN** `RunnerController.list_accept_env_namespaces()` is awaited
- **THEN** the returned list MUST equal `["accept-req-1", "accept-req-2"]`
- **AND** the fallback prefix filter MUST NOT be invoked

#### Scenario: AEGC-S8 empty label result triggers prefix fallback

- **GIVEN** the K8s API returns an empty list for label selector
  `sisyphus/role=accept-env`, but returns `["accept-req-1", "other-ns"]` when
  listing all namespaces
- **WHEN** `RunnerController.list_accept_env_namespaces()` is awaited
- **THEN** the returned list MUST equal `["accept-req-1"]`
- **AND** `"other-ns"` MUST be excluded because it does not match the prefix

### Requirement: RunnerController.delete_namespace SHALL be idempotent

The orchestrator SHALL expose `RunnerController.delete_namespace(name: str) ->
None` that calls the K8s API to delete the named namespace. It MUST log an INFO
message on successful deletion. If the K8s API raises `ApiException(status=404)`,
the method MUST treat it as a no-op and return without re-raising. Any other
ApiException MUST propagate to the caller.

#### Scenario: AEGC-S9 successful deletion logs and returns

- **GIVEN** a namespace named `"accept-req-1"` exists in the cluster
- **WHEN** `RunnerController.delete_namespace("accept-req-1")` is awaited
- **THEN** the K8s `delete_namespace` API MUST be invoked exactly once
- **AND** an INFO log containing `"runner.namespace.deleted"` MUST be emitted

#### Scenario: AEGC-S10 404 is silently ignored

- **GIVEN** a namespace named `"accept-req-1"` does not exist (K8s returns 404)
- **WHEN** `RunnerController.delete_namespace("accept-req-1")` is awaited
- **THEN** the method MUST return without raising
- **AND** no error log MUST be emitted

### Requirement: accept_env_gc.run_loop SHALL run gc_once periodically and handle cancellation gracefully

The orchestrator SHALL expose `accept_env_gc.run_loop()` that runs an infinite
loop: await `gc_once()`, log the result, then sleep for
`settings.accept_env_gc_interval_sec` seconds. If `gc_once()` raises any
exception other than `asyncio.CancelledError`, the loop MUST catch it, log an
ERROR, and continue to the next iteration. `asyncio.CancelledError` MUST be
re-raised so that the orchestrator shutdown can cancel the background task
cleanly.

#### Scenario: AEGC-S11 normal tick logs result and continues

- **GIVEN** `accept_env_gc_interval_sec` is set to a small value (e.g. 1),
  and `gc_once()` returns `{"cleaned_namespaces": []}` on the first tick
- **WHEN** `run_loop()` is started and allowed to run for at least 2 ticks
- **THEN** each tick MUST call `gc_once()`
- **AND** a DEBUG log `accept_env_gc.tick` MUST be emitted for each tick

#### Scenario: AEGC-S12 exception in gc_once is logged but loop continues

- **GIVEN** `accept_env_gc_interval_sec` is set to a small value, and the first
  `gc_once()` call raises `RuntimeError("boom")`
- **WHEN** `run_loop()` is started and allowed to run for at least 2 ticks
- **THEN** an ERROR log containing `"accept_env_gc.loop.error"` MUST be emitted
- **AND** the loop MUST continue to the second tick

### Requirement: main.py startup SHALL start accept_env_gc when K8s controller is available and interval > 0

The orchestrator SHALL start the `accept_env_gc.run_loop()` background task in
`main.py` startup if and only if: (1) the K8s runner controller was successfully
initialized in the same startup try-block, AND (2)
`settings.accept_env_gc_interval_sec > 0`. The task MUST be appended to the
`_bg_tasks` list so that shutdown cancels it. If the controller initialization
fails (e.g. dev environment without kubeconfig), the accept_env_gc loop MUST NOT
be started.

#### Scenario: AEGC-S13 startup starts loop when controller OK and interval > 0

- **GIVEN** K8s controller initialization succeeds AND
  `accept_env_gc_interval_sec = 900`
- **WHEN** `startup()` is called
- **THEN** an asyncio Task named `"accept_env_gc"` MUST be created and appended
  to `_bg_tasks`

#### Scenario: AEGC-S14 startup skips loop when controller fails

- **GIVEN** K8s controller initialization raises an exception (e.g. no kubeconfig)
- **WHEN** `startup()` is called
- **THEN** no accept_env_gc background task MUST be created
- **AND** a WARNING log MUST be emitted but startup MUST succeed

### Requirement: admin endpoints SHALL expose manual trigger and status for accept env GC

The orchestrator SHALL expose two admin endpoints: `POST /admin/accept-env-gc`
(requires Bearer token authorization) that triggers one `gc_once()` pass and
returns its result dict; and `GET /admin/accept-env-gc/status` (no authorization
required) that returns `{"last": <last_result_or_null>}`. Both endpoints MUST
behave identically to their runner-gc counterparts.

#### Scenario: AEGC-S15 manual trigger returns GC result

- **GIVEN** a valid Bearer token in the Authorization header
- **WHEN** `POST /admin/accept-env-gc` is called
- **THEN** `gc_once()` MUST be invoked exactly once
- **AND** the HTTP response MUST contain the result dict

#### Scenario: AEGC-S16 status endpoint returns last result without auth

- **GIVEN** no Authorization header
- **WHEN** `GET /admin/accept-env-gc/status` is called
- **THEN** the endpoint MUST return HTTP 200
- **AND** the response body MUST contain `{"last": null}` before any GC has run
