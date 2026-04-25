"""Contract tests for REQ-default-agent-model-sonnet-1777131381.

Capability: agent-model-default
Author: challenger-agent (black-box, written from spec only)

Scenarios:
  DAMS-S1  Settings.agent_model resolves to claude-sonnet-4-6 without env override
  DAMS-S2  Settings.agent_model is overridable via SISYPHUS_AGENT_MODEL env
"""
from __future__ import annotations

import importlib


def test_dams_s1_agent_model_defaults_to_claude_sonnet_4_6(monkeypatch) -> None:
    """DAMS-S1: Without SISYPHUS_AGENT_MODEL env set, Settings().agent_model
    MUST equal 'claude-sonnet-4-6'.

    GIVEN  SISYPHUS_AGENT_MODEL is not set
    WHEN   Settings() is instantiated
    THEN   settings.agent_model == "claude-sonnet-4-6"
    """
    monkeypatch.delenv("SISYPHUS_AGENT_MODEL", raising=False)

    from orchestrator import config as config_mod

    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.agent_model == "claude-sonnet-4-6", (
        f"settings.agent_model must default to 'claude-sonnet-4-6' when "
        f"SISYPHUS_AGENT_MODEL is not set; got {reloaded.settings.agent_model!r}. "
        f"The previous default (None) resolved to BKD's per-engine default "
        f"(claude-opus), which is the most expensive model."
    )


def test_dams_s2_agent_model_overridable_via_env(monkeypatch) -> None:
    """DAMS-S2: When SISYPHUS_AGENT_MODEL=claude-haiku-4-5, Settings().agent_model
    MUST equal 'claude-haiku-4-5'.

    GIVEN  SISYPHUS_AGENT_MODEL is set to "claude-haiku-4-5"
    WHEN   Settings() is instantiated
    THEN   settings.agent_model == "claude-haiku-4-5"
    """
    monkeypatch.setenv("SISYPHUS_AGENT_MODEL", "claude-haiku-4-5")

    from orchestrator import config as config_mod

    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.agent_model == "claude-haiku-4-5", (
        f"settings.agent_model must honour SISYPHUS_AGENT_MODEL override; "
        f"expected 'claude-haiku-4-5', got {reloaded.settings.agent_model!r}"
    )

    # Restore to sonnet default so subsequent tests see the correct default.
    monkeypatch.delenv("SISYPHUS_AGENT_MODEL", raising=False)
    importlib.reload(config_mod)
