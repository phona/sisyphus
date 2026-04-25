# helm-default-involved-repos delta

## ADDED Requirements

### Requirement: helm values.yaml MUST ship default_involved_repos=[phona/sisyphus] for self-dogfood single-repo deploy

The `orchestrator/helm/values.yaml` file SHALL define
`env.default_involved_repos` with the literal default value
`["phona/sisyphus"]`. This chart MUST ship the sisyphus self-deployment
opinion (the chart's `image.repository` is `ghcr.io/phona/sisyphus-orchestrator`,
i.e. it deploys sisyphus itself), so a fresh `helm install` of the
unmodified chart MUST produce an orchestrator Pod whose
`SISYPHUS_DEFAULT_INVOLVED_REPOS` resolves to `phona/sisyphus`. Multi-repo
or non-sisyphus deployments MUST override this list in their own values
file. The value SHALL be a YAML sequence of strings (not a comma-string)
to keep configuration human-editable.

#### Scenario: HDIR-S1 values.yaml ships [phona/sisyphus] as the env default

- **GIVEN** the file `orchestrator/helm/values.yaml` parsed as YAML
- **WHEN** the contract test reads `env.default_involved_repos`
- **THEN** the field MUST exist
- **AND** its value MUST equal the list `["phona/sisyphus"]`

### Requirement: helm configmap.yaml MUST conditionally inject SISYPHUS_DEFAULT_INVOLVED_REPOS as JSON from values.env.default_involved_repos

The template `orchestrator/helm/templates/configmap.yaml` SHALL emit a
`SISYPHUS_DEFAULT_INVOLVED_REPOS` ConfigMap key only when
`.Values.env.default_involved_repos` is non-empty. The emitted value
MUST be a JSON-encoded array of the list entries (e.g.
`'["phona/sisyphus"]'`), produced via the `toJson` template helper, so
pydantic-settings v2 decodes it natively into a `list[str]` (its default
list-field decoder is JSON; a comma-separated string would raise
`SettingsError` at orchestrator startup). When the list is empty or
absent, the key MUST be omitted entirely so the orchestrator Pod's
Settings object falls back to its `default_factory=list` default of
`[]`. The wiring MUST live inside a `{{- with ... }}` conditional block
keyed on `.Values.env.default_involved_repos`.

#### Scenario: HDIR-S2 configmap.yaml wires SISYPHUS_DEFAULT_INVOLVED_REPOS conditionally as JSON

- **GIVEN** the file `orchestrator/helm/templates/configmap.yaml`
  read as text
- **WHEN** the contract test searches its content
- **THEN** the file MUST contain the literal string
  `SISYPHUS_DEFAULT_INVOLVED_REPOS`
- **AND** the file MUST contain the literal string
  `.Values.env.default_involved_repos` inside a `{{- with ... }}`
  conditional block (so the key is omitted when the list is empty)
- **AND** the file MUST contain `toJson` within the same conditional
  block (so the env value is a valid JSON array consumable by
  pydantic-settings, not a csv string)

### Requirement: Settings MUST parse SISYPHUS_DEFAULT_INVOLVED_REPOS JSON env into a string list

Settings (defined in `orchestrator/src/orchestrator/config.py`) SHALL accept the env var `SISYPHUS_DEFAULT_INVOLVED_REPOS` as a JSON-encoded array string such as `["phona/a","phona/b"]` and MUST resolve `settings.default_involved_repos` to the corresponding list of strings such as `["phona/a","phona/b"]`.
This behavior MUST also hold for a single-element JSON value such as
`["phona/sisyphus"]` resolving to `["phona/sisyphus"]`, since that is
the shape the helm-injected ConfigMap will produce in self-dogfood
deployments. This requirement MUST be exercised by an in-process test
that constructs Settings against a temporarily-patched env, so a
regression in pydantic-settings list-parsing (or in the helm template
swapping back to csv encoding) fails the orchestrator contract suite.

#### Scenario: HDIR-S3 Settings parses JSON env into a string list

- **GIVEN** environment variables `SISYPHUS_BKD_TOKEN`,
  `SISYPHUS_WEBHOOK_TOKEN`, `SISYPHUS_PG_DSN` are set to non-empty stubs
- **AND** environment variable
  `SISYPHUS_DEFAULT_INVOLVED_REPOS=["phona/a","phona/b"]` is set
- **WHEN** the contract test instantiates `Settings()` fresh
- **THEN** `settings.default_involved_repos` MUST equal
  `["phona/a", "phona/b"]`
- **AND** when the env var is set to the single-element JSON
  `["phona/sisyphus"]`, the resolved list MUST equal `["phona/sisyphus"]`
