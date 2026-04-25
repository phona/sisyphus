# agent-model-default Specification

## Purpose
TBD - created by archiving change REQ-default-agent-model-sonnet-1777131381. Update Purpose after archive.
## Requirements
### Requirement: Settings.agent_model MUST default to claude-sonnet-4-6

The `orchestrator/src/orchestrator/config.py` Settings class SHALL define
`agent_model` with a default value of `"claude-sonnet-4-6"` (not `None`).
All sisyphus-dispatched sub-agents (verifier, fixer, accept, pr_ci_watch,
done_archive, staging_test) MUST use this model when no override is provided
via the `SISYPHUS_AGENT_MODEL` environment variable. The previous default
(`None`, which caused BKD to fall back to its per-engine default of claude-opus)
MUST no longer be the behaviour of a freshly installed orchestrator without
explicit env overrides.

#### Scenario: DAMS-S1 Settings.agent_model resolves to claude-sonnet-4-6 without env override

- **GIVEN** environment variable `SISYPHUS_AGENT_MODEL` is not set
- **WHEN** `Settings()` is instantiated
- **THEN** `settings.agent_model` MUST equal `"claude-sonnet-4-6"`

#### Scenario: DAMS-S2 Settings.agent_model is overridable via SISYPHUS_AGENT_MODEL env

- **GIVEN** environment variable `SISYPHUS_AGENT_MODEL` is set to `"claude-haiku-4-5"`
- **WHEN** `Settings()` is instantiated
- **THEN** `settings.agent_model` MUST equal `"claude-haiku-4-5"`

