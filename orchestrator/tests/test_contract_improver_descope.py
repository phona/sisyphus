"""
Contract tests for IMPROVER daemon descope (REQ-544).

Verifies that documentation does not imply or describe an automated IMPROVER
daemon. The improvement loop is human-driven; config_version + improvement_log
are consumed by people via Metabase, not by any automated system.

Scenarios: IMPR-S1 through IMPR-S5 (per openspec/changes/REQ-544/specs/descope-improver-documentation/spec.md).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DOCS_ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
DOCS_OBSERVABILITY = REPO_ROOT / "docs" / "observability.md"
DOCS_IMPACT_REPORT = REPO_ROOT / "docs" / "IMPACT-REPORT.md"


# ──────────────────────────────────────────────────────────────────────────
# IMPR-S1: architecture.md §0.4 removes IMPROVER agent branding
# ──────────────────────────────────────────────────────────────────────────

def test_architecture_section_0_4_no_improver_branding():
    """IMPR-S1: docs/architecture.md §0.4 must not contain 'IMPROVER' in the heading."""
    text = DOCS_ARCHITECTURE.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("### 0.4"):
            assert "IMPROVER" not in line, (
                f"docs/architecture.md §0.4 heading must not contain 'IMPROVER': {line!r}"
            )
            return
    raise AssertionError("docs/architecture.md must have a '### 0.4' section")


# ──────────────────────────────────────────────────────────────────────────
# IMPR-S2: architecture.md §10 clarifies human-driven improvement loop
# ──────────────────────────────────────────────────────────────────────────

def test_architecture_section_10_human_driven_explicit():
    """IMPR-S2: docs/architecture.md §10 must explicitly state human-driven and no daemon."""
    text = DOCS_ARCHITECTURE.read_text()
    # Find the §10 area ("## 10. 观测系统")
    section_start = text.find("## 10. 观测系统")
    assert section_start != -1, "docs/architecture.md must have '## 10. 观测系统' section"
    section_text = text[section_start:section_start + 2000]

    assert "人工驱动" in section_text, (
        "docs/architecture.md §10 must explicitly state the loop is '人工驱动'"
    )
    assert "不存在" in section_text and "IMPROVER daemon" in section_text, (
        "docs/architecture.md §10 must explicitly state there is no IMPROVER daemon"
    )


# ──────────────────────────────────────────────────────────────────────────
# IMPR-S3: observability.md strengthens human-driven language
# ──────────────────────────────────────────────────────────────────────────

def test_observability_human_driven_explicit():
    """IMPR-S3: docs/observability.md sustainable improvement loop must state '人工驱动'."""
    text = DOCS_OBSERVABILITY.read_text()
    # Find the sustainable improvement loop section
    section_start = text.find("## 可持续改进闭环")
    assert section_start != -1, "docs/observability.md must have '## 可持续改进闭环' section"
    section_text = text[section_start:]

    assert "人工驱动" in section_text, (
        "docs/observability.md sustainable improvement loop must contain '人工驱动'"
    )
    assert "consumer" in section_text.lower() or "消费者" in section_text, (
        "docs/observability.md must clarify who consumes the dashboards"
    )


# ──────────────────────────────────────────────────────────────────────────
# IMPR-S4: IMPACT-REPORT.md removes automated self-improvement implication
# ──────────────────────────────────────────────────────────────────────────

def test_impact_report_no_self_improvement_phrasing():
    """IMPR-S4: docs/IMPACT-REPORT.md must not frame improvement_log as automated self-improvement."""
    text = DOCS_IMPACT_REPORT.read_text()
    assert "系统自我改进" not in text, (
        "docs/IMPACT-REPORT.md must not contain '系统自我改进'"
    )
    assert "TODO：当前未启用" not in text, (
        "docs/IMPACT-REPORT.md must not contain 'TODO：当前未启用' implying a planned automated feature"
    )


def test_impact_report_human_hypothesis_framing():
    """IMPR-S4: docs/IMPACT-REPORT.md must frame improvement_log as human hypothesis tracking."""
    text = DOCS_IMPACT_REPORT.read_text()
    assert "人工改进假设追踪" in text or "人写假设" in text or "human" in text.lower(), (
        "docs/IMPACT-REPORT.md must frame improvement_log as human-driven hypothesis tracking"
    )
