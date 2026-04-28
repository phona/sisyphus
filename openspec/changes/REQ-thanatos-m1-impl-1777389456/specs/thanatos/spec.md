## ADDED Requirements

### Requirement: thanatos HTTP driver executes REST API acceptance scenarios

The HTTP driver SHALL implement the five-method async contract defined in `drivers/base.py`. It MUST support `preflight` via `GET {endpoint}/healthz`, `act` via parsing When steps like `POST /api/v1/order with body {"foo":"bar"}`, and `assert_` via parsing Then steps like `response code is 200` or `response body.order_id > 0`. JSONPath-style dot notation MUST be supported for body field access.

#### Scenario: THAN-M1-S1 HTTP preflight returns ok on 200 healthz
- **GIVEN** a mock HTTP server returning 200 on /healthz
- **WHEN** HttpDriver.preflight("http://localhost:8080") is called
- **THEN** result.ok is True

#### Scenario: THAN-M1-S2 HTTP act executes POST with JSON body
- **GIVEN** HttpDriver initialized
- **WHEN** act('POST /api/order with body {"id":1}') is called
- **THEN** an HTTP POST is sent to /api/order with body {"id":1}

#### Scenario: THAN-M1-S3 HTTP assert_ checks status code
- **GIVEN** last response status code is 201
- **WHEN** assert_("response code is 201") is called
- **THEN** result.ok is True

#### Scenario: THAN-M1-S4 HTTP assert_ checks JSON body path
- **GIVEN** last response body is {"order":{"id":42}}
- **WHEN** assert_("response body.order.id is 42") is called
- **THEN** result.ok is True

### Requirement: thanatos runner executes full scenario flow

The runner SHALL load skill, parse spec, pick driver, preflight, then execute given/when/then steps in order. On any assertion failure it MUST call capture_evidence and return a ScenarioResult with passed=False.

#### Scenario: THAN-M1-S5 runner executes all steps and reports pass
- **GIVEN** a spec with one scenario containing Given/When/Then
- **WHEN** run_all(skill_path, spec_path, endpoint) is called
- **THEN** all scenarios return passed=True with step results

#### Scenario: THAN-M1-S6 runner captures evidence on assert failure
- **GIVEN** a spec with a Then step that will fail
- **WHEN** run_scenario is called
- **THEN** result.passed is False and evidence is attached to the failing step

### Requirement: create_accept dispatches thanatos MCP path

create_accept SHALL run make accept-env-up, parse the endpoint JSON for a `thanatos` block, and dispatch an accept-agent BKD issue with thanatos parameters. If the `thanatos` block is absent it MUST fallback to the v0.3-lite shell script path.

#### Scenario: THAN-M1-S7 create_accept with thanatos block dispatches agent
- **GIVEN** accept-env-up returns endpoint JSON containing thanatos block
- **WHEN** create_accept is called
- **THEN** a BKD accept-agent issue is created with thanatos parameters

#### Scenario: THAN-M1-S8 create_accept without thanatos block falls back to lite
- **GIVEN** accept-env-up returns endpoint JSON without thanatos block
- **WHEN** create_accept is called
- **THEN** the v0.3-lite shell script path is executed
