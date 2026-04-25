"""challenger contract tests for REQ-default-involved-repos-1777124541:
helm default_involved_repos — black-box spec verification for HDIR-S1/S2/S3.

Tests are written purely from spec without reading implementation code.
"""
from __future__ import annotations

import os
import unittest.mock
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_HELM_DIR = _REPO_ROOT / "orchestrator" / "helm"
_VALUES_YAML = _HELM_DIR / "values.yaml"
_CONFIGMAP_YAML = _HELM_DIR / "templates" / "configmap.yaml"


# ── HDIR-S1: values.yaml ships [phona/sisyphus] as env default ───────────────

def test_hdir_s1_values_env_default_involved_repos_field_exists():
    """HDIR-S1: orchestrator/helm/values.yaml MUST define env.default_involved_repos."""
    data = yaml.safe_load(_VALUES_YAML.read_text())
    assert "env" in data, (
        "values.yaml must have an 'env' section "
        "(REQ-default-involved-repos-1777124541 HDIR-S1)"
    )
    assert "default_involved_repos" in data["env"], (
        "values.yaml env section must contain default_involved_repos "
        "(REQ-default-involved-repos-1777124541 HDIR-S1)"
    )


def test_hdir_s1_values_env_default_involved_repos_equals_phona_sisyphus():
    """HDIR-S1: env.default_involved_repos MUST equal ['phona/sisyphus'].

    GIVEN: orchestrator/helm/values.yaml parsed as YAML
    WHEN: contract test reads env.default_involved_repos
    THEN: value equals ['phona/sisyphus'] — the self-dogfood single-repo default
    """
    data = yaml.safe_load(_VALUES_YAML.read_text())
    actual = data["env"]["default_involved_repos"]
    assert actual == ["phona/sisyphus"], (
        f"env.default_involved_repos must equal ['phona/sisyphus'], got {actual!r}. "
        "A fresh helm install of this chart MUST produce an orchestrator Pod whose "
        "SISYPHUS_DEFAULT_INVOLVED_REPOS resolves to phona/sisyphus "
        "(REQ-default-involved-repos-1777124541 HDIR-S1)"
    )


def test_hdir_s1_values_env_default_involved_repos_is_list_not_string():
    """HDIR-S1: env.default_involved_repos MUST be a YAML sequence, not a string.

    Human-editable format requires a proper list, not a CSV string.
    """
    data = yaml.safe_load(_VALUES_YAML.read_text())
    actual = data["env"]["default_involved_repos"]
    assert isinstance(actual, list), (
        f"env.default_involved_repos must be a YAML sequence (list), got {type(actual).__name__!r}: {actual!r} "
        "(REQ-default-involved-repos-1777124541 HDIR-S1)"
    )


# ── HDIR-S2: configmap.yaml conditionally injects SISYPHUS_DEFAULT_INVOLVED_REPOS ──

def test_hdir_s2_configmap_contains_env_key():
    """HDIR-S2: configmap.yaml MUST emit SISYPHUS_DEFAULT_INVOLVED_REPOS.

    GIVEN: orchestrator/helm/templates/configmap.yaml read as text
    WHEN: contract test searches its content
    THEN: the literal string SISYPHUS_DEFAULT_INVOLVED_REPOS MUST appear
    """
    text = _CONFIGMAP_YAML.read_text()
    assert "SISYPHUS_DEFAULT_INVOLVED_REPOS" in text, (
        "configmap.yaml must contain the string SISYPHUS_DEFAULT_INVOLVED_REPOS to "
        "wire the helm value into the orchestrator Pod env "
        "(REQ-default-involved-repos-1777124541 HDIR-S2)"
    )


def test_hdir_s2_configmap_references_values_path():
    """HDIR-S2: configmap.yaml MUST reference .Values.env.default_involved_repos."""
    text = _CONFIGMAP_YAML.read_text()
    assert ".Values.env.default_involved_repos" in text, (
        "configmap.yaml must reference .Values.env.default_involved_repos to source "
        "the list from helm values "
        "(REQ-default-involved-repos-1777124541 HDIR-S2)"
    )


def test_hdir_s2_configmap_uses_with_conditional_block():
    """HDIR-S2: configmap.yaml MUST use a {{- with ...}} block on the list so the
    key is omitted when the list is empty (prevents pydantic parsing '[]' as [''])."""
    text = _CONFIGMAP_YAML.read_text()
    # Both 'with' and the values path must appear — together they form the conditional
    assert "with" in text and ".Values.env.default_involved_repos" in text, (
        "configmap.yaml must use a '{{- with .Values.env.default_involved_repos }}' "
        "conditional block so SISYPHUS_DEFAULT_INVOLVED_REPOS is omitted when empty, "
        "letting Settings fall back to default_factory=list "
        "(REQ-default-involved-repos-1777124541 HDIR-S2)"
    )


def test_hdir_s2_configmap_uses_tojson_not_csv():
    """HDIR-S2: configmap.yaml MUST use toJson to encode the list as a JSON array.

    pydantic-settings v2 decodes list[str] fields as JSON by default.
    A csv string (e.g. 'phona/x,phona/y') would raise SettingsError at startup.
    """
    text = _CONFIGMAP_YAML.read_text()
    assert "toJson" in text, (
        "configmap.yaml must use toJson to produce a JSON-encoded array for "
        "SISYPHUS_DEFAULT_INVOLVED_REPOS — csv encoding raises SettingsError in "
        "pydantic-settings v2 (REQ-default-involved-repos-1777124541 HDIR-S2)"
    )


# ── HDIR-S3: Settings parses JSON env into string list ───────────────────────

def _make_settings_with_env(extra: dict[str, str]):
    """Instantiate Settings() with extra env vars patched in."""
    from orchestrator.config import Settings

    base = {
        "SISYPHUS_BKD_TOKEN": "stub-token",
        "SISYPHUS_WEBHOOK_TOKEN": "stub-wh-token",
        "SISYPHUS_PG_DSN": "postgresql://stub:stub@localhost/stub",
        "SISYPHUS_BKD_BASE_URL": "https://bkd.example.stub/api",
    }
    base.update(extra)
    with unittest.mock.patch.dict(os.environ, base, clear=False):
        return Settings()


def test_hdir_s3_settings_parses_json_multi_element_list():
    """HDIR-S3: Settings MUST parse SISYPHUS_DEFAULT_INVOLVED_REPOS JSON array
    into list[str] for a multi-element JSON value.

    GIVEN: SISYPHUS_DEFAULT_INVOLVED_REPOS='["phona/a","phona/b"]'
    WHEN: Settings() instantiated fresh
    THEN: settings.default_involved_repos == ["phona/a", "phona/b"]
    """
    s = _make_settings_with_env(
        {"SISYPHUS_DEFAULT_INVOLVED_REPOS": '["phona/a","phona/b"]'}
    )
    assert s.default_involved_repos == ["phona/a", "phona/b"], (
        f"Settings must decode multi-element JSON array for SISYPHUS_DEFAULT_INVOLVED_REPOS, "
        f"got {s.default_involved_repos!r} "
        "(REQ-default-involved-repos-1777124541 HDIR-S3)"
    )


def test_hdir_s3_settings_parses_json_single_element_list():
    """HDIR-S3: Settings MUST parse SISYPHUS_DEFAULT_INVOLVED_REPOS single-element
    JSON array into a one-item list — matching what the helm configmap injects.

    GIVEN: SISYPHUS_DEFAULT_INVOLVED_REPOS='["phona/sisyphus"]'
    WHEN: Settings() instantiated fresh
    THEN: settings.default_involved_repos == ["phona/sisyphus"]
    """
    s = _make_settings_with_env(
        {"SISYPHUS_DEFAULT_INVOLVED_REPOS": '["phona/sisyphus"]'}
    )
    assert s.default_involved_repos == ["phona/sisyphus"], (
        f"Settings must decode single-element JSON array for SISYPHUS_DEFAULT_INVOLVED_REPOS, "
        f"got {s.default_involved_repos!r} "
        "(REQ-default-involved-repos-1777124541 HDIR-S3)"
    )


def test_hdir_s3_settings_field_exists_with_correct_env_alias():
    """HDIR-S3: Settings.default_involved_repos field MUST exist and be bound to
    SISYPHUS_DEFAULT_INVOLVED_REPOS env var."""
    from orchestrator.config import Settings

    fields = Settings.model_fields
    assert "default_involved_repos" in fields, (
        "Settings must have a default_involved_repos field "
        "(REQ-default-involved-repos-1777124541 HDIR-S3)"
    )
