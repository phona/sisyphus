## ADDED Requirements

### Requirement: Architecture documentation must not imply an automated IMPROVER daemon exists or is planned

The system SHALL document the improvement loop as explicitly human-driven. No documentation file SHALL use naming or phrasing that suggests an automated daemon consumes `config_version` or `improvement_log` tables, reads Metabase dashboards, or auto-modifies prompts without human decision.

#### Scenario: IMPR-S1 architecture.md removes IMPROVER agent branding

- **GIVEN** `docs/architecture.md` exists
- **WHEN** a reader inspects §0.4 (repository role permissions)
- **THEN** the section title does not contain "IMPROVER" and does not name any automated improvement agent
- **AND** the section clearly describes push/merge permissions for the sisyphus maintainer AI assistant role, not a daemon

#### Scenario: IMPR-S2 architecture.md clarifies human-driven improvement loop

- **GIVEN** `docs/architecture.md` exists
- **WHEN** a reader inspects §10 (observability system description)
- **THEN** the text explicitly states that `config_version` and `improvement_log` are consumed by humans via Metabase dashboards
- **AND** the text explicitly states there is no automated IMPROVER daemon

#### Scenario: IMPR-S3 observability.md strengthens human-driven language

- **GIVEN** `docs/observability.md` exists
- **WHEN** a reader inspects the sustainable improvement loop section
- **THEN** the text contains an explicit statement that the loop is "人工驱动" (human-driven)
- **AND** the text clarifies that Metabase SQL consumers are people, not machines

#### Scenario: IMPR-S4 IMPACT-REPORT.md removes automated self-improvement implication

- **GIVEN** `docs/IMPACT-REPORT.md` exists
- **WHEN** a reader inspects the observability design section describing `improvement_log`
- **THEN** the description does not contain phrases like "系统自我改进" (system self-improvement) or "TODO：当前未启用" (TODO: not currently enabled) that imply a planned automated feature
- **AND** the description frames `improvement_log` as human hypothesis tracking

#### Scenario: IMPR-S5 contract tests prevent regression

- **GIVEN** the test suite runs
- **WHEN** `test_contract_improver_descope.py` executes
- **THEN** all tests pass, verifying that no documentation file reintroduces IMPROVER daemon misinterpretation
