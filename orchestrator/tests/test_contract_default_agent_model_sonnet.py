"""Contract regression for REQ-default-agent-model-sonnet-1777131381.

Settings.agent_model MUST default to "claude-sonnet-4-6" so that all
sisyphus-dispatched sub-agents (verifier, fixer, accept, pr_ci_watch,
done_archive, staging_test) use sonnet instead of the implicit BKD
per-engine default (claude-opus) when no explicit model override is given.

Scenarios covered:
  DAMS-S1  Settings.agent_model == "claude-sonnet-4-6" without env override
  DAMS-S2  SISYPHUS_AGENT_MODEL env overrides the default
"""
from __future__ import annotations

import importlib


def test_dams_s1_agent_model_defaults_to_sonnet(monkeypatch) -> None:
    """DAMS-S1: without SISYPHUS_AGENT_MODEL env, settings.agent_model
    MUST equal 'claude-sonnet-4-6' (not None, not opus).
    """
    from orchestrator import config as config_mod

    monkeypatch.delenv("SISYPHUS_AGENT_MODEL", raising=False)
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.agent_model == "claude-sonnet-4-6", (
        f"settings.agent_model MUST default to 'claude-sonnet-4-6'; "
        f"got {reloaded.settings.agent_model!r} "
        f"(REQ-default-agent-model-sonnet-1777131381)"
    )


def test_dams_s2_agent_model_overridable_via_env(monkeypatch) -> None:
    """DAMS-S2: SISYPHUS_AGENT_MODEL env MUST override the default, e.g.
    to 'claude-haiku-4-5' for cost-sensitive test environments.
    """
    from orchestrator import config as config_mod

    monkeypatch.setenv("SISYPHUS_AGENT_MODEL", "claude-haiku-4-5")
    reloaded = importlib.reload(config_mod)
    assert reloaded.settings.agent_model == "claude-haiku-4-5", (
        f"SISYPHUS_AGENT_MODEL=claude-haiku-4-5 MUST override the default; "
        f"got {reloaded.settings.agent_model!r}"
    )

    # Restore to default
    monkeypatch.delenv("SISYPHUS_AGENT_MODEL", raising=False)
    importlib.reload(config_mod)
