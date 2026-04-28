# cleanup-runner-zombie

## ADDED Requirements

### Requirement: /admin/req/{req_id}/escalate must schedule runner Pod cleanup after the SQL state update

The orchestrator SHALL, immediately after the raw SQL UPDATE pushing
`req_state.state` to `escalated`, schedule a fire-and-forget
`asyncio.Task` that runs
`engine._cleanup_runner_on_terminal(req_id, ReqState.ESCALATED)` so the
runner Pod is deleted within seconds. This MUST happen before the
endpoint returns its 200 response. The task MUST be tracked in a
module-level `set[asyncio.Task]` (mirroring the existing
`_complete_cleanup_tasks` pattern in `admin.complete`) with a
`done_callback` that removes the task from the set so the asyncio event
loop does not garbage-collect the in-flight task. Without this, the
runner Pod owned by the REQ stays alive for the entire
`pvc_retain_on_escalate_days` window because `runner_gc._active_req_ids`
treats `escalated` REQs within retention as active and skips them in
`gc_orphans`.

#### Scenario: FRE-S1 force_escalate on in-flight REQ schedules cleanup task with ESCALATED terminal state

- **GIVEN** a REQ row with `state='analyzing'` exists in `req_state` and
  the K8s runner controller is initialized
- **WHEN** the client sends `POST /admin/req/REQ-X/escalate` with a valid
  Bearer token
- **THEN** the endpoint MUST execute one SQL UPDATE setting
  `state='escalated'` on the row keyed by `req_id='REQ-X'`
- **AND** the response MUST be 200 with JSON body containing
  `action == "force_escalated"` and `from_state == "analyzing"`
- **AND** an `asyncio.Task` running
  `engine._cleanup_runner_on_terminal('REQ-X', ReqState.ESCALATED)`
  MUST be scheduled (fire-and-forget) before the endpoint returns,
  so that one event loop tick later the cleanup callable has been
  invoked exactly once with those arguments

### Requirement: /admin/req/{req_id}/escalate noop branch on already-escalated REQ MUST NOT re-schedule cleanup

The orchestrator MUST return HTTP 200 with `action == "noop"` when
`force_escalate` is invoked on a REQ already in state `escalated`, and
MUST NOT execute any SQL UPDATE and MUST NOT schedule a cleanup task. This is
because the prior `force_escalate` call (or the canonical
transition-driven path into `escalated`) already deleted the Pod, and a
second cleanup wastes K8s API calls and adds a spurious warning log
entry. The runner Pod is owned by `engine._cleanup_runner_on_terminal`
on the original `escalated` transition; a second call here would either
404 (Pod already gone) or race with whatever debug session a human is
running.

#### Scenario: FRE-S2 second force_escalate on already-escalated REQ is a 200 noop with no cleanup

- **GIVEN** a REQ row with `state='escalated'` exists
- **WHEN** the client sends `POST /admin/req/REQ-X/escalate`
- **THEN** the response MUST be 200 with body containing
  `action == "noop"` and `state == "already escalated"`
- **AND** no SQL UPDATE on `req_state` MUST be executed
- **AND** no `asyncio.Task` running `_cleanup_runner_on_terminal` MUST
  be scheduled by this call

### Requirement: /admin/req/{req_id}/escalate returns 404 before any SQL UPDATE or cleanup scheduling when the REQ is unknown

The endpoint MUST return HTTP 404 Not Found when no row matches the
`req_id` path parameter. This MUST happen before any SQL UPDATE or
cleanup-task scheduling, so that "no such REQ" cannot be confused with
"REQ in wrong state" and so that no cleanup attempt is made for a
non-existent runner.

#### Scenario: FRE-S3 unknown REQ returns 404 with no side effects

- **GIVEN** no row in `req_state` with `req_id='REQ-DOES-NOT-EXIST'`
- **WHEN** the client sends `POST /admin/req/REQ-DOES-NOT-EXIST/escalate`
- **THEN** the response MUST be 404 with detail containing the literal
  substring `not found`
- **AND** no SQL UPDATE on `req_state` MUST be executed
- **AND** no cleanup task MUST be scheduled

### Requirement: /admin/req/{req_id}/escalate retains the PVC across the cleanup task

The cleanup task scheduled by `force_escalate` MUST pass
`ReqState.ESCALATED` (not `ReqState.DONE`) as the `terminal_state`
argument so that `_cleanup_runner_on_terminal` deletes the Pod but
keeps the PVC for the human-debug retention window. The system MUST
NOT pass `ReqState.DONE` here even though `force_escalate` writes
`escalated_reason='admin'` to context, because PVC retention semantics
depend on the terminal-state argument and breaking PVC retention would
remove the workspace before a human or follow-up verifier issue can
inspect it.

#### Scenario: FRE-S4 cleanup task argument is ReqState.ESCALATED so retain_pvc=True semantics apply

- **GIVEN** a REQ row with `state='analyzing'` exists
- **WHEN** the client sends `POST /admin/req/REQ-X/escalate`
- **THEN** the scheduled cleanup task MUST be invoked with
  `terminal_state == ReqState.ESCALATED`
- **AND** `_cleanup_runner_on_terminal` (which derives
  `retain_pvc=(terminal_state == ReqState.ESCALATED)`) MUST therefore
  call `cleanup_runner` with `retain_pvc=True`, leaving the PVC in
  place for the configured retention window
