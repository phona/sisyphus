## ADDED Requirements

### Requirement: orchestrator gates fresh REQ entry on in-flight count and node disk pressure

The sisyphus orchestrator SHALL run an admission check at the start of the
two fresh-entry actions — `start_intake` and `start_analyze` — and MUST
reject the REQ before any runner Pod / PVC creation when either
condition holds:

1. The number of REQs whose state is not in
   `{init, done, escalated, gh-incident-open}` (excluding the calling
   REQ itself) is greater than or equal to
   `settings.inflight_req_cap`. A cap value of `0` SHALL disable this
   check unconditionally.
2. The K8s node's disk usage ratio reported by
   `RunnerController.node_disk_usage_ratio()` is greater than or equal
   to `settings.admission_disk_pressure_threshold`.

Rejection MUST be expressed by returning
`{"emit": "verify.escalate", "reason": <human-readable string>}` from
the action handler, after writing
`ctx.escalated_reason = "rate-limit:inflight-cap-exceeded"` or
`"rate-limit:disk-pressure"` so the existing escalate pathway tags the
REQ with the correct reason. The continuation action
`start_analyze_with_finalized_intent` MUST NOT run the gate — that REQ
already passed admission at intake time.

The disk-pressure check SHALL fail open (admit + log warning) when:
- the runner controller is not initialised (development without K8s),
- the existing `runner_gc._DISK_CHECK_DISABLED` short-circuit flag is
  set (cluster-scoped `nodes:list` denied by RBAC), or
- the underlying `node_disk_usage_ratio()` raises any exception other
  than what the GC loop already handles.

#### Scenario: ORCH-RATE-S1 cap=0 disables the in-flight gate

- **GIVEN** `settings.inflight_req_cap = 0` and 50 active REQs in
  `req_state`
- **WHEN** `check_admission(pool, req_id="REQ-new")` runs
- **THEN** the result's `admit` is `True` regardless of the count

#### Scenario: ORCH-RATE-S2 in-flight count under cap admits

- **GIVEN** `settings.inflight_req_cap = 10` and the SQL count of
  non-terminal REQs other than `REQ-new` is `9`
- **WHEN** `check_admission(pool, req_id="REQ-new")` runs
- **THEN** the result's `admit` is `True` and `reason` is `None`

#### Scenario: ORCH-RATE-S3 in-flight count at cap rejects

- **GIVEN** `settings.inflight_req_cap = 10` and the SQL count of
  non-terminal REQs other than `REQ-new` is `10`
- **WHEN** `check_admission(pool, req_id="REQ-new")` runs
- **THEN** the result's `admit` is `False` and `reason` MUST contain
  the substring `inflight-cap-exceeded`

#### Scenario: ORCH-RATE-S4 disk usage under threshold admits

- **GIVEN** `settings.admission_disk_pressure_threshold = 0.75` and
  `node_disk_usage_ratio()` returns `0.50`
- **WHEN** `check_admission(pool, req_id="REQ-new")` runs with the
  in-flight count well below the cap
- **THEN** the result's `admit` is `True`

#### Scenario: ORCH-RATE-S5 disk usage above threshold rejects

- **GIVEN** `settings.admission_disk_pressure_threshold = 0.75` and
  `node_disk_usage_ratio()` returns `0.80`
- **WHEN** `check_admission(pool, req_id="REQ-new")` runs with the
  in-flight count well below the cap
- **THEN** the result's `admit` is `False` and `reason` MUST contain
  the substring `disk-pressure`

#### Scenario: ORCH-RATE-S6 missing runner controller fails open

- **GIVEN** `k8s_runner.get_controller()` raises `RuntimeError`
  (development environment without K8s)
- **WHEN** `check_admission(pool, req_id="REQ-new")` runs
- **THEN** the result's `admit` is `True` (fail open) and disk check
  is skipped without raising
