## ADDED Requirements

### Requirement: IMPROVER daemon descoped in favor of human-driven improvement loop

The system SHALL NOT implement an automated IMPROVER daemon. The `config_version` + `improvement_log` tables are retained for **human-driven** improvement tracking. The documentation SHALL clearly state that the improvement loop is manual and SHALL explain why automation is deferred.

#### Scenario: SIS-531-S1 architecture.md documents the human-driven loop
- **GIVEN** a reader opens docs/architecture.md
- **WHEN** they read §10 (观测系统)
- **THEN** the text explicitly states that `config_version` + `improvement_log` support a human-driven improvement loop, with a clear explanation of why automation is not pursued at this time

#### Scenario: SIS-531-S2 no residual IMPROVER daemon references exist
- **GIVEN** a grep for "IMPROVER daemon" across the repository
- **WHEN** all matches are examined
- **THEN** every occurrence is either in the descope context (explaining the decision) or in the example-reqs.yaml gap-analysis file (updated to reflect descope)

#### Scenario: SIS-531-S3 IMPACT-REPORT.md accurately describes improvement_log
- **GIVEN** a reader opens docs/IMPACT-REPORT.md
- **WHEN** they read the observability tables section
- **THEN** `improvement_log` is described as a human improvement hypothesis tracker, not as a TODO for an automated system
