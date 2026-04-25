## ADDED Requirements

### Requirement: accept_env_gc cleans orphaned accept namespaces

The system SHALL provide a background GC task (`accept_env_gc.run_loop`) that
periodically scans Kubernetes namespaces matching the pattern `accept-req-*` and
deletes those whose corresponding REQ is in a terminal state (done or escalated
past the retention window). The GC MUST NOT delete namespaces for REQs that are
still in a non-terminal (in-flight) state. Deletion MUST be performed by calling
`kubernetes.client.CoreV1Api.delete_namespace` (cascading all resources inside)
rather than `helm uninstall`, so the implementation requires no helm RBAC.

#### Scenario: AEGC-S1 done REQ의 accept namespace는 gc_once에서 삭제된다

- **GIVEN** req_state에 state=done인 REQ-foo-123이 있음
- **AND** K8s에 `accept-req-foo-123` namespace가 존재함
- **WHEN** `gc_once()` 가 호출됨
- **THEN** `core_v1.delete_namespace("accept-req-foo-123")` 가 호출됨
- **AND** 반환값 `cleaned` 리스트에 `"accept-req-foo-123"` 이 포함됨

#### Scenario: AEGC-S2 in-flight REQ의 accept namespace는 삭제되지 않는다

- **GIVEN** req_state에 state=accept-running인 REQ-foo-123이 있음
- **AND** K8s에 `accept-req-foo-123` namespace가 존재함
- **WHEN** `gc_once()` 가 호출됨
- **THEN** `core_v1.delete_namespace` 가 호출되지 않음
- **AND** 반환값 `cleaned` 는 빈 리스트

#### Scenario: AEGC-S3 escalated이고 retention 기간 내이면 삭제되지 않는다

- **GIVEN** req_state에 state=escalated, updated_at=2시간 전인 REQ-foo-123이 있음
- **AND** pvc_retain_on_escalate_days=1 (기본값)
- **AND** K8s에 `accept-req-foo-123` namespace가 존재함
- **WHEN** `gc_once()` 가 호출됨
- **THEN** `core_v1.delete_namespace` 가 호출되지 않음

#### Scenario: AEGC-S4 escalated이고 retention 초과이면 삭제된다

- **GIVEN** req_state에 state=escalated, updated_at=30일 전인 REQ-foo-123이 있음
- **AND** K8s에 `accept-req-foo-123` namespace가 존재함
- **WHEN** `gc_once()` 가 호출됨
- **THEN** `core_v1.delete_namespace("accept-req-foo-123")` 가 호출됨

### Requirement: accept_env_gc degrades gracefully on RBAC 403

The system SHALL detect Kubernetes API 403 (Forbidden) on `list_namespace` or
`delete_namespace` calls and treat them as a permanent RBAC denial for the
process lifetime. On the first 403, the system MUST emit exactly one
`accept_env_gc.rbac_denied` log at INFO level and set a process-level flag
`_NS_RBAC_DISABLED = True`. On all subsequent GC ticks, the system MUST skip
the K8s API call entirely without emitting any additional log. The module MUST
also skip immediately when no runner controller is initialized (dev/test
environment).

#### Scenario: AEGC-S5 K8s controller 없으면 skipped 반환

- **GIVEN** k8s_runner controller가 초기화되어 있지 않음
- **WHEN** `gc_once()` 가 호출됨
- **THEN** `{"skipped": "no runner controller"}` 를 반환함
- **AND** K8s API 호출이 없음

#### Scenario: AEGC-S6 list_namespace 403 → 진행 불가 디스에이블

- **GIVEN** `core_v1.list_namespace` 가 `ApiException(status=403)` 를 raise함
- **WHEN** `gc_once()` 가 호출됨
- **THEN** `_NS_RBAC_DISABLED` 가 True로 설정됨
- **AND** INFO 레벨로 `accept_env_gc.rbac_denied` 가 한 번 기록됨
- **AND** 반환값에 `"skipped"` 키가 포함됨

#### Scenario: AEGC-S7 _NS_RBAC_DISABLED=True이면 list_namespace 호출 스킵

- **GIVEN** `_NS_RBAC_DISABLED` 가 True임
- **WHEN** `gc_once()` 가 호출됨
- **THEN** `core_v1.list_namespace` 가 호출되지 않음
- **AND** `{"skipped": "namespace rbac disabled"}` 를 반환함
