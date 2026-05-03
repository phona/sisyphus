# Proposal: Fix chart snapshot_exclude_project_ids JSON encoding (closes #343)

## Problem

Deploying sisyphus at sha-f6868a4 caused the orchestrator pod to enter
CrashLoopBackOff with:

```
pydantic_settings.exceptions.SettingsError: error parsing value for field
"snapshot_exclude_project_ids" from source "EnvSettingsSource"
```

Two coupled bugs in `orchestrator/helm`:

1. **`templates/configmap.yaml` encodes the list as csv via `join "," .`**
   while every other `list[str]` env (`default_involved_repos`,
   `gh_incident_labels`, `default_base_branches`, the M-preflight
   capability lists) uses `toJson .`. pydantic-settings v2's default
   decoder for `list[str]` is JSON; csv triggers `SettingsError` at boot.
   The same configmap.yaml even has an explicit comment on
   `default_involved_repos` calling this out — `snapshot_exclude_project_ids`
   was simply missed during the JSON migration.

2. **`values.yaml` ships `snapshot_exclude_project_ids: [77k9z58j]`** as
   the chart default. `77k9z58j` is the `workflow-test` BKD project,
   which has been archived since April; the snapshot loop's
   `SELECT DISTINCT project_id FROM req_state` would never produce it
   anyway, so the exclusion is dead config. Worse, shipping a non-empty
   default kept bug 1 latent on every fresh `helm install` — anyone
   deploying the unmodified chart hits the crash on first boot.

## Solution

Two-line fix in the chart + one comment fix in config.py + a contract
regression test. Mirror the existing `default_involved_repos` shape
exactly (same `{{- with }}` + `toJson` pattern, same JSON-only env
contract).

- `orchestrator/helm/templates/configmap.yaml` — `join "," .` → `toJson .`
  and add the same JSON-vs-csv warning comment that
  `default_involved_repos` already carries.
- `orchestrator/helm/values.yaml` — change the default from
  `[77k9z58j]` to `[]`. New dead projects can still be added via
  `--set 'env.snapshot_exclude_project_ids={proj-a,proj-b}'` / values
  override at deploy time.
- `orchestrator/src/orchestrator/config.py` — fix the misleading docstring
  on the field that claims "env 用逗号分隔或 JSON 数组"; in pydantic-settings
  v2 only JSON works for `list[str]`. Comment now matches reality and
  references issue #343.
- `orchestrator/tests/test_contract_helm_snapshot_exclude_project_ids.py`
  — new contract test (3 scenarios CSEPI-S1..S3) that mirrors
  `test_contract_helm_default_involved_repos.py`. Asserts the values
  default, the configmap template shape, and the round-trip
  Settings parse for a JSON array env.

## Scope

- `orchestrator/helm/templates/configmap.yaml`
- `orchestrator/helm/values.yaml`
- `orchestrator/src/orchestrator/config.py` (comment only)
- `orchestrator/tests/test_contract_helm_snapshot_exclude_project_ids.py` (new)
- `openspec/changes/REQ-fix-chart-snapshot-exclude-1777808452/**`

## Out of scope

- Other chart fields already on `toJson` (no change needed).
- Snapshot loop runtime behavior (`orchestrator/src/orchestrator/snapshot.py`
  reads `settings.snapshot_exclude_project_ids` via Settings — the bug is
  pre-Settings, in env→Settings parsing).
- Adding a custom csv decoder. Sisyphus already standardizes on JSON for
  every other compound env field; introducing a csv path would be a
  divergence with no upside.
- Migrating callers that today pass `--set 'env.snapshot_exclude_project_ids=foo'`
  on the CLI. helm `--set` for a YAML list works (`--set 'env.snapshot_exclude_project_ids={foo,bar}'`),
  and the configmap template will still emit a JSON array in that case.
