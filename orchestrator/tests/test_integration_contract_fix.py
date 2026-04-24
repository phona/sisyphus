"""Contract tests for REQ-acceptance-e2e-1777045998 capability: integration-contract-fix.

Scenarios: SISP-S1, SISP-S2
Black-box: reads only docs/integration-contracts.md, no orchestrator internals imported.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_DOCS = pathlib.Path(__file__).parent.parent.parent / "docs" / "integration-contracts.md"


def _text() -> str:
    assert _DOCS.exists(), f"docs file not found: {_DOCS}"
    return _DOCS.read_text(encoding="utf-8")


def _section_42(text: str) -> str:
    """Return the text of §4.2 up to the next same-level heading."""
    m = re.search(r"(###\s*4\.2\b.*?)(?=\n###\s|\Z)", text, re.DOTALL)
    assert m, "docs must contain a §4.2 section"
    return m.group(1)


# ---------------------------------------------------------------------------
# Scenario SISP-S1: canonical target names are present; obsolete ones are absent
# ---------------------------------------------------------------------------

def test_sisp_s1_canonical_targets_present():
    """docs must contain ci-accept-env-up and ci-accept-env-down."""
    text = _text()
    assert "ci-accept-env-up" in text, \
        "integration-contracts.md must contain 'ci-accept-env-up'"
    assert "ci-accept-env-down" in text, \
        "integration-contracts.md must contain 'ci-accept-env-down'"


def test_sisp_s1_obsolete_accept_up_absent():
    """'accept-up' must not appear as a standalone make target (normative context)."""
    text = _text()
    for i, line in enumerate(text.splitlines(), 1):
        # Allow lines that also mention the canonical name (e.g. rename notes),
        # but reject lines that reference accept-up as a target on its own.
        stripped = line.strip()
        if re.search(r"\baccept-up\b", stripped):
            # Only fail if this is a normative make-target reference, not a
            # parenthetical "was called accept-up" historical note alongside the
            # canonical name.
            if "ci-accept-env-up" not in stripped:
                pytest.fail(
                    f"Line {i}: obsolete target 'accept-up' without "
                    f"'ci-accept-env-up' context: {line!r}"
                )


def test_sisp_s1_obsolete_accept_down_absent():
    """'accept-down' must not appear as a standalone make target (normative context)."""
    text = _text()
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if re.search(r"\baccept-down\b", stripped):
            if "ci-accept-env-down" not in stripped:
                pytest.fail(
                    f"Line {i}: obsolete target 'accept-down' without "
                    f"'ci-accept-env-down' context: {line!r}"
                )


# ---------------------------------------------------------------------------
# Scenario SISP-S2: §4.2 template uses Docker Compose, not kubectl/helm
# ---------------------------------------------------------------------------

def test_sisp_s2_section_42_uses_docker_compose():
    """§4.2 integration repo template must reference Docker Compose."""
    section = _section_42(_text())
    has_compose = bool(
        re.search(r"docker[\s_-]*compose", section, re.IGNORECASE)
    )
    assert has_compose, \
        "§4.2 must use Docker Compose in the integration repo template"


def test_sisp_s2_section_42_no_kubectl_commands():
    """§4.2 template must not prescribe kubectl commands."""
    section = _section_42(_text())
    kubectl_lines = [
        line for line in section.splitlines()
        if line.strip().startswith("kubectl")
    ]
    assert not kubectl_lines, (
        f"§4.2 template must not contain kubectl commands; found: {kubectl_lines}"
    )


def test_sisp_s2_note_runner_no_kubectl():
    """docs must note that sisyphus runner pods have no kubectl/helm."""
    text = _text()
    has_note = bool(
        re.search(r"(没有|no|without)\s*(kubectl|helm)", text, re.IGNORECASE)
    )
    assert has_note, \
        "integration-contracts.md must note that runner pods have no kubectl/helm"
