"""Challenger contract test for REQ-fix-chart-snapshot-exclude-1777808452.

Black-box derived from openspec/changes/REQ-fix-chart-snapshot-exclude-1777808452/
specs/helm-snapshot-exclude-project-ids/spec.md (scenarios CSEPI-S1..S3).

Independent of dev's test_contract_helm_snapshot_exclude_project_ids.py: written
against the published spec only, not the implementation. If both pass we have
double coverage; if they diverge the spec is ambiguous and needs spec_fixer.
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
    exist and equal `[]`. The historical default `[77k9z58j]` references the
    archived `workflow-test` BKD project and made bug #343 latent on every
    fresh `helm install`.
    """
    parsed = yaml.safe_load(_VALUES_YAML.read_text(encoding="utf-8"))
    env = parsed.get("env")
    assert isinstance(env, dict), "values.yaml must have an `env:` mapping"
    assert "snapshot_exclude_project_ids" in env, (
        "values.yaml must define `env.snapshot_exclude_project_ids` "
        "(REQ-fix-chart-snapshot-exclude-1777808452)"
    )
    assert env["snapshot_exclude_project_ids"] == [], (
        f"env.snapshot_exclude_project_ids must default to [] (the "
        f"historical [77k9z58j] referenced an archived BKD project and "
        f"shipped bug #343 latent on every fresh helm install); got "
        f"{env['snapshot_exclude_project_ids']!r}"
    )


def test_csepi_s2_configmap_emits_snapshot_exclude_as_json_inside_with_block() -> None:
    """CSEPI-S2: configmap.yaml MUST emit
    `SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS` only when the values list is
    non-empty, sourced via `{{- with .Values.env.snapshot_exclude_project_ids }}`,
    encoded with `toJson` (NOT `join "," .`). pydantic-settings v2's default
    `list[str]` decoder is JSON; csv triggers SettingsError at boot (#343).
    """
    text = _CONFIGMAP_TPL.read_text(encoding="utf-8")
    assert "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS" in text, (
        "configmap.yaml must emit SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS "
        "(REQ-fix-chart-snapshot-exclude-1777808452)"
    )

    block_start = text.find("SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS")
    assert block_start != -1
    # Spec mandates a ~400-char window around the key contains all the
    # required template pieces. The `{{- with }}` line precedes the env
    # key (often with intervening comment lines), so weight the window
    # toward preceding text.
    window_start = max(0, block_start - 400)
    window = text[window_start : block_start + 400]

    assert "with .Values.env.snapshot_exclude_project_ids" in window, (
        "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS must sit inside "
        "`{{- with .Values.env.snapshot_exclude_project_ids }}` so the "
        "key is omitted when the list is empty (Settings then falls back "
        "to its default_factory=list of [])"
    )
    assert "toJson" in window, (
        "list must be JSON-encoded via `toJson`; pydantic-settings v2's "
        "default `list[str]` decoder is JSON, csv would crash boot (#343)"
    )
    assert 'join "," .' not in window, (
        "configmap MUST NOT use `join \",\" .` for "
        "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS (root cause of #343)"
    )


def test_csepi_s3_settings_parses_json_env_into_string_list(monkeypatch) -> None:
    """CSEPI-S3: SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS as a JSON-encoded
    array MUST resolve `settings.snapshot_exclude_project_ids` to the
    matching list[str], for both multi-element and single-element shapes.
    """
    from orchestrator import config as config_mod

    # Multi-element JSON
    monkeypatch.setenv(
        "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS", '["proj-a","proj-b"]'
    )
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.snapshot_exclude_project_ids == ["proj-a", "proj-b"], (
        f'JSON \'["proj-a","proj-b"]\' must resolve to '
        f"['proj-a','proj-b']; got "
        f"{reloaded.settings.snapshot_exclude_project_ids!r}"
    )

    # Single element
    monkeypatch.setenv(
        "SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS", '["only-one"]'
    )
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.snapshot_exclude_project_ids == ["only-one"], (
        f'JSON \'["only-one"]\' must resolve to ["only-one"]; '
        f"got {reloaded.settings.snapshot_exclude_project_ids!r}"
    )

    # Cleanup: restore Settings to conftest defaults so other tests see [].
    monkeypatch.delenv("SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS", raising=False)
    importlib.reload(config_mod)
