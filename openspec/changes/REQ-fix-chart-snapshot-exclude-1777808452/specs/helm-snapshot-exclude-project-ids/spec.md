# helm-snapshot-exclude-project-ids

## ADDED Requirements

### Requirement: helm values.yaml MUST default snapshot_exclude_project_ids to an empty list

The `orchestrator/helm/values.yaml` file SHALL define
`env.snapshot_exclude_project_ids` with the literal default value `[]`.
The chart MUST NOT ship any pre-populated dead project id (the historical
default `[77k9z58j]` referenced the `workflow-test` BKD project that has
been archived since April; the snapshot loop's
`SELECT DISTINCT project_id FROM req_state` would never produce it
anyway). Operators that need to exclude additional dead projects SHALL
override this list in their own values file or via
`--set 'env.snapshot_exclude_project_ids={proj-a,proj-b}'`. The value
MUST be a YAML sequence of strings (not a comma-string) to keep
configuration human-editable.

#### Scenario: CSEPI-S1 values.yaml ships [] as the env default

- **GIVEN** the file `orchestrator/helm/values.yaml` parsed as YAML
- **WHEN** the contract test reads `env.snapshot_exclude_project_ids`
- **THEN** the field MUST exist
- **AND** its value MUST equal the empty list `[]`

### Requirement: helm configmap.yaml MUST conditionally inject SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS as JSON from values.env.snapshot_exclude_project_ids

The template `orchestrator/helm/templates/configmap.yaml` SHALL emit a
`SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS` ConfigMap key only when
`.Values.env.snapshot_exclude_project_ids` is non-empty. The emitted
value MUST be a JSON-encoded array of the list entries (e.g.
`'["proj-a","proj-b"]'`), produced via the `toJson` template helper, so
pydantic-settings v2 decodes it natively into a `list[str]` (its default
list-field decoder is JSON; a comma-separated string raises
`SettingsError` at orchestrator boot — issue #343). When the list is
empty or absent, the key MUST be omitted entirely so the orchestrator
Pod's Settings object falls back to its `default_factory=list` default
of `[]`. The template MUST NOT use `join "," .` for this field. The
wiring MUST live inside a `{{- with ... }}` conditional block keyed on
`.Values.env.snapshot_exclude_project_ids`.

#### Scenario: CSEPI-S2 configmap.yaml emits SNAPSHOT_EXCLUDE as JSON inside a `{{- with }}` block

- **GIVEN** `orchestrator/helm/templates/configmap.yaml` read as text
- **WHEN** the contract test searches for `SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS`
- **THEN** the key MUST be present
- **AND** the surrounding ~400 chars MUST contain `with .Values.env.snapshot_exclude_project_ids`
- **AND** the surrounding ~400 chars MUST contain `toJson`
- **AND** the surrounding ~400 chars MUST NOT contain `join "," .`

### Requirement: orchestrator Settings MUST parse JSON-encoded SNAPSHOT_EXCLUDE env into a string list

Settings SHALL parse the env var SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS
as a JSON-encoded array of strings and resolve the field
snapshot_exclude_project_ids to the matching list of strings. The field
SHALL default to an empty list when the env var is unset. The contract
MUST hold for both multi-element and single-element JSON arrays.

#### Scenario: CSEPI-S3 multi-element JSON env resolves to list[str]

- **GIVEN** env `SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS='["proj-a","proj-b"]'`
- **WHEN** `orchestrator.config` is reloaded
- **THEN** `settings.snapshot_exclude_project_ids` MUST equal `["proj-a", "proj-b"]`
- **AND** when env is changed to `'["only-one"]'` and config reloaded
- **THEN** `settings.snapshot_exclude_project_ids` MUST equal `["only-one"]`
