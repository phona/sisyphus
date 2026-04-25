"""Contract regression for REQ-default-involved-repos-1777124541.

helm chart MUST ship `env.default_involved_repos: [phona/sisyphus]` and the
configmap template MUST conditionally inject `SISYPHUS_DEFAULT_INVOLVED_REPOS`
as JSON, so a fresh `helm install` of this chart (sisyphus self-deployment)
gives the orchestrator Pod the L4 fallback that REQ-clone-fallback-direct-analyze-1777119520
made the Settings field load.

Scenarios covered:
  HDIR-S1 values.yaml ships [phona/sisyphus] as the env default
  HDIR-S2 configmap.yaml wires SISYPHUS_DEFAULT_INVOLVED_REPOS as JSON conditionally
  HDIR-S3 Settings parses JSON env into a string list
"""
from __future__ import annotations

import importlib
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent          # orchestrator/
_HELM_DIR = _REPO_ROOT / "helm"
_VALUES_YAML = _HELM_DIR / "values.yaml"
_CONFIGMAP_TPL = _HELM_DIR / "templates" / "configmap.yaml"


def test_hdir_s1_values_yaml_ships_phona_sisyphus_as_env_default() -> None:
    """HDIR-S1: helm/values.yaml `env.default_involved_repos` must equal
    `["phona/sisyphus"]` so a fresh `helm install` of the unmodified chart
    produces an orchestrator Pod whose L4 fallback resolves to phona/sisyphus.
    """
    parsed = yaml.safe_load(_VALUES_YAML.read_text(encoding="utf-8"))
    env = parsed.get("env")
    assert isinstance(env, dict), "values.yaml must have an `env:` mapping"
    assert "default_involved_repos" in env, (
        "values.yaml must define `env.default_involved_repos` "
        "(REQ-default-involved-repos-1777124541)"
    )
    assert env["default_involved_repos"] == ["phona/sisyphus"], (
        f"env.default_involved_repos must equal ['phona/sisyphus'] for "
        f"sisyphus self-dogfood single-repo deploy; got "
        f"{env['default_involved_repos']!r}"
    )


def test_hdir_s2_configmap_wires_sisyphus_default_involved_repos_as_json_conditionally() -> None:
    """HDIR-S2: configmap.yaml must conditionally inject
    `SISYPHUS_DEFAULT_INVOLVED_REPOS` from `.Values.env.default_involved_repos`
    using the `{{- with ... }}` + `toJson` pattern. Empty list MUST omit
    the key so Settings falls back to its `default_factory=list` default of
    `[]`. JSON encoding (not csv) is required because pydantic-settings v2's
    default decoder for `list[str]` env values is JSON; csv triggers a
    SettingsError at orchestrator startup.
    """
    text = _CONFIGMAP_TPL.read_text(encoding="utf-8")
    assert "SISYPHUS_DEFAULT_INVOLVED_REPOS" in text, (
        "configmap.yaml must emit SISYPHUS_DEFAULT_INVOLVED_REPOS "
        "(REQ-default-involved-repos-1777124541)"
    )
    assert ".Values.env.default_involved_repos" in text, (
        "configmap.yaml must source from .Values.env.default_involved_repos"
    )

    # Locate the conditional block and assert structural pieces co-exist.
    # We do not parse Go-template AST; substring presence within ~10 lines of
    # the env-key line is a sufficient guard for the stable template shape.
    block_start = text.find(".Values.env.default_involved_repos")
    assert block_start != -1
    # ~300-char window covers `{{- with ... }}` ... `{{ toJson . | quote }}` ... `{{- end }}`
    window_start = max(0, block_start - 100)
    window = text[window_start : block_start + 300]
    assert "with .Values.env.default_involved_repos" in window, (
        "SISYPHUS_DEFAULT_INVOLVED_REPOS must sit inside "
        "`{{- with .Values.env.default_involved_repos }}` so the key is "
        "omitted when the list is empty"
    )
    assert "toJson" in window, (
        "list must be JSON-encoded via `toJson`; pydantic-settings v2's "
        "default `list[str]` decoder is JSON, csv would crash boot"
    )


def test_hdir_s3_settings_parses_json_env_into_string_list(monkeypatch) -> None:
    """HDIR-S3: SISYPHUS_DEFAULT_INVOLVED_REPOS as a JSON-encoded array MUST
    resolve `settings.default_involved_repos` to the matching list[str], for
    both multi-element and single-element shapes. Single-element exercises
    the exact shape helm injects in self-dogfood deploys
    (`'["phona/sisyphus"]'`).
    """
    from orchestrator import config as config_mod

    # Multi-element JSON
    monkeypatch.setenv(
        "SISYPHUS_DEFAULT_INVOLVED_REPOS", '["phona/a","phona/b"]'
    )
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.default_involved_repos == ["phona/a", "phona/b"], (
        f'JSON \'["phona/a","phona/b"]\' must resolve to '
        f"['phona/a','phona/b']; got {reloaded.settings.default_involved_repos!r}"
    )

    # Single element (self-dogfood shape)
    monkeypatch.setenv(
        "SISYPHUS_DEFAULT_INVOLVED_REPOS", '["phona/sisyphus"]'
    )
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.default_involved_repos == ["phona/sisyphus"], (
        f'JSON \'["phona/sisyphus"]\' must resolve to ["phona/sisyphus"]; '
        f"got {reloaded.settings.default_involved_repos!r}"
    )

    # Cleanup: restore Settings to the conftest defaults so other tests see []
    monkeypatch.delenv("SISYPHUS_DEFAULT_INVOLVED_REPOS", raising=False)
    importlib.reload(config_mod)
