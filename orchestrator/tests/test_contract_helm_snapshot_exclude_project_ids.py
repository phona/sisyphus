"""Contract regression for REQ-fix-chart-snapshot-exclude-1777808452 (closes #343).

helm chart MUST encode `env.snapshot_exclude_project_ids` as a JSON array
in the rendered ConfigMap, mirroring `default_involved_repos` /
`gh_incident_labels`. A csv-encoded value (e.g. `77k9z58j`) crashes
orchestrator boot with `pydantic_settings.exceptions.SettingsError` because
pydantic-settings v2's default `list[str]` decoder is JSON, not csv.

Additionally, the shipped default value MUST be `[]` — the historical
default `[77k9z58j]` (workflow-test) is dead config because that BKD
project has been archived for months; keeping it as a default just made
the csv-encoding bug latent in every fresh helm install.

Scenarios covered:
  CSEPI-S1 values.yaml ships [] as the env default
  CSEPI-S2 configmap.yaml wires SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS as JSON conditionally
  CSEPI-S3 Settings parses JSON env into a string list
"""
from __future__ import annotations

import importlib
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent          # orchestrator/
_HELM_DIR = _REPO_ROOT / "helm"
_VALUES_YAML = _HELM_DIR / "values.yaml"
_CONFIGMAP_TPL = _HELM_DIR / "templates" / "configmap.yaml"


def test_csepi_s1_values_yaml_ships_empty_list_as_env_default() -> None:
    """CSEPI-S1: helm/values.yaml `env.snapshot_exclude_project_ids` MUST
    default to `[]`. The previous default `[77k9z58j]` (workflow-test) is
    dead config — that BKD project has been archived and the snapshot loop
    would never list it anyway. Shipping a non-empty default also kept the
    csv-encoding crash bug latent on every fresh `helm install`.
    """
    parsed = yaml.safe_load(_VALUES_YAML.read_text(encoding="utf-8"))
    env = parsed.get("env")
    assert isinstance(env, dict), "values.yaml must have an `env:` mapping"
    assert "snapshot_exclude_project_ids" in env, (
        "values.yaml must define `env.snapshot_exclude_project_ids` "
        "(REQ-fix-chart-snapshot-exclude-1777808452)"
    )
    assert env["snapshot_exclude_project_ids"] == [], (
        f"env.snapshot_exclude_project_ids must default to [] now that "
        f"workflow-test (77k9z58j) is archived; got "
        f"{env['snapshot_exclude_project_ids']!r}"
    )


def test_csepi_s2_configmap_wires_snapshot_exclude_as_json_conditionally() -> None:
    """CSEPI-S2: configmap.yaml MUST conditionally inject
    `SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS` from
    `.Values.env.snapshot_exclude_project_ids` using the
    `{{- with ... }}` + `toJson` pattern. Empty list MUST omit the key so
    Settings falls back to `default_factory=list` ([]). JSON encoding (not
    csv) is required because pydantic-settings v2's default decoder for
    `list[str]` env values is JSON; csv triggers SettingsError at
    orchestrator startup (issue #343).
    """
    text = _CONFIGMAP_TPL.read_text(encoding="utf-8")
    assert "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS" in text, (
        "configmap.yaml must emit SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS"
    )
    assert ".Values.env.snapshot_exclude_project_ids" in text, (
        "configmap.yaml must source from .Values.env.snapshot_exclude_project_ids"
    )

    block_start = text.find(".Values.env.snapshot_exclude_project_ids")
    assert block_start != -1
    window_start = max(0, block_start - 100)
    window = text[window_start : block_start + 400]
    assert "with .Values.env.snapshot_exclude_project_ids" in window, (
        "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS must sit inside "
        "`{{- with .Values.env.snapshot_exclude_project_ids }}` so the "
        "key is omitted when the list is empty"
    )
    assert "toJson" in window, (
        "list must be JSON-encoded via `toJson`; pydantic-settings v2's "
        "default `list[str]` decoder is JSON, csv would crash boot (#343)"
    )
    assert 'join "," .' not in window, (
        "csv encoding via `join \",\" .` MUST NOT be used — it crashes "
        "orchestrator boot under pydantic-settings v2 (#343)"
    )


def test_csepi_s3_settings_parses_json_env_into_string_list(monkeypatch) -> None:
    """CSEPI-S3: SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS as a JSON-encoded
    array MUST resolve `settings.snapshot_exclude_project_ids` to the
    matching list[str], for both multi-element and single-element shapes.
    """
    from orchestrator import config as config_mod

    monkeypatch.setenv(
        "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS", '["proj-a","proj-b"]'
    )
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.snapshot_exclude_project_ids == [
        "proj-a",
        "proj-b",
    ], (
        f'JSON \'["proj-a","proj-b"]\' must resolve to '
        f"['proj-a','proj-b']; got "
        f"{reloaded.settings.snapshot_exclude_project_ids!r}"
    )

    monkeypatch.setenv(
        "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS", '["only-one"]'
    )
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.snapshot_exclude_project_ids == ["only-one"], (
        f'JSON \'["only-one"]\' must resolve to ["only-one"]; got '
        f"{reloaded.settings.snapshot_exclude_project_ids!r}"
    )

    monkeypatch.delenv("SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS", raising=False)
    importlib.reload(config_mod)
