## ADDED Requirements

### Requirement: POST /admin/runner-gc triggers an immediate GC pass

The system SHALL expose `POST /admin/runner-gc` that immediately runs a single
GC pass (`gc_once()`) and returns the result as JSON. The endpoint MUST require
a valid `Authorization: Bearer <webhook_token>` header (same token as other admin
endpoints) and return HTTP 401 on missing or invalid token. When no K8s runner
controller is available the endpoint MUST return HTTP 200 with `{"skipped": "..."}`.
On success the response MUST include `cleaned_pods`, `cleaned_pvcs`, `pod_kept`,
`pvc_kept`, `disk_pressure`, and `ran_at` (UTC ISO-8601 string).

#### Scenario: RGA-S1 POST /admin/runner-gc returns split GC result with ran_at
- **GIVEN** a valid Bearer token and K8s controller available
- **WHEN** client sends POST /admin/runner-gc
- **THEN** response is 200 with JSON containing cleaned_pods, cleaned_pvcs,
  pod_kept, pvc_kept, disk_pressure, and ran_at (non-empty string)

#### Scenario: RGA-S2 POST /admin/runner-gc with no controller returns skipped
- **GIVEN** no K8s runner controller initialized
- **WHEN** client sends POST /admin/runner-gc with valid token
- **THEN** response is 200 with JSON containing key "skipped"

#### Scenario: RGA-S3 POST /admin/runner-gc without token returns 401
- **GIVEN** no Authorization header
- **WHEN** client sends POST /admin/runner-gc
- **THEN** response is 401

### Requirement: GET /admin/runner-gc/status exposes last GC run result

The system SHALL expose `GET /admin/runner-gc/status` returning the in-memory
result of the most recent `gc_once()` execution (from either the periodic loop
or an admin trigger). The endpoint MUST NOT require authentication (read-only
operational status). Before any GC pass has run, the response MUST be
`{"last": null}`. After at least one GC pass, the response MUST include a
`last` object with `ran_at` and the GC metrics from that pass.

#### Scenario: RGA-S4 GET /admin/runner-gc/status before any GC returns null
- **GIVEN** orchestrator just started, no GC pass has run
- **WHEN** client sends GET /admin/runner-gc/status
- **THEN** response is 200 with JSON `{"last": null}`

#### Scenario: RGA-S5 GET /admin/runner-gc/status after GC returns last result
- **GIVEN** at least one GC pass has completed (timer or admin trigger)
- **WHEN** client sends GET /admin/runner-gc/status
- **THEN** response is 200 with JSON containing last.ran_at and last.cleaned_pods
